"""Administrator pages: users, element library, import profiles, settings, audit trail."""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from tims_core.elements import ElementDefinitionError, load_element_text
from tims_core.parsing import ImportProfileError, load_profile_text

from . import db
from .auth import password_problem
from .models import ROLE_NAMES, ROLES, AuditLog, User, now
from .services import audit, get_setting, import_profiles, library, reload_library, set_setting
from .views_util import role_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


# ----------------------------------------------------------------- users

@bp.route("/users")
@role_required("admin")
def users():
    rows = db.session.scalars(select(User).order_by(User.active.desc(), User.full_name)).all()
    return render_template("admin/users.html", users=rows, role_names=ROLE_NAMES)


@bp.route("/users/new", methods=["GET", "POST"])
@bp.route("/users/<int:pk>", methods=["GET", "POST"])
@role_required("admin")
def user_edit(pk: int | None = None):
    user = db.session.get(User, pk) if pk else None
    if pk and user is None:
        abort(404)
    temp_password = None
    if request.method == "POST":
        f = request.form
        username = f.get("username", "").strip()
        data = {"full_name": f.get("full_name", "").strip(), "designation": f.get("designation", "").strip(),
                "role": f.get("role", "analyst")}
        errors = []
        if not data["full_name"]:
            errors.append("Full name is required.")
        if data["role"] not in ROLES:
            errors.append("Choose a role.")
        if user is None:
            if not re.fullmatch(r"[A-Za-z0-9._-]{3,64}", username):
                errors.append("Username must be 3–64 letters, digits, dots, dashes or underscores.")
            elif db.session.scalars(select(User).where(User.username == username)).first():
                errors.append("That username is already taken.")
        if user is not None and user.id == current_user.id and data["role"] != "admin":
            errors.append("You cannot remove your own administrator role.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/user_form.html", user=user, form=f, roles=ROLES, role_names=ROLE_NAMES)
        if user is None:
            temp_password = secrets.token_urlsafe(9)
            user = User(username=username, must_change_password=True, **data)
            user.set_password(temp_password)
            db.session.add(user)
            audit("user_created", "user", username, data)
        else:
            changes = {k: (getattr(user, k), v) for k, v in data.items() if getattr(user, k) != v}
            for k, v in data.items():
                setattr(user, k, v)
            action = f.get("action")
            if action == "disable" and user.id != current_user.id:
                user.active = False
                changes["active"] = (True, False)
            elif action == "enable":
                user.active = True
                user.failed_logins, user.locked_until = 0, None
                changes["active"] = (False, True)
            elif action == "reset":
                temp_password = secrets.token_urlsafe(9)
                user.set_password(temp_password)
                user.must_change_password = True
                user.failed_logins, user.locked_until = 0, None
                changes["password"] = "reset"
            audit("user_changed", "user", user.username, changes)
        db.session.commit()
        if temp_password:
            flash(f"Temporary password for {user.username}: {temp_password}  "
                  "(give it to the user; they must change it at first login).", "info")
        else:
            flash("User saved.", "info")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", user=user, form={}, roles=ROLES, role_names=ROLE_NAMES)


# ----------------------------------------------------------------- element and import-profile files

KINDS = {
    "elements": {"title": "Element definitions", "dir": "ELEMENTS_DIR"},
    "profiles": {"title": "Import formats", "dir": "IMPORT_PROFILES_DIR"},
}


def kind_dir(kind: str) -> Path:
    return Path(current_app.config[KINDS[kind]["dir"]])


def validate_text(kind: str, text: str, name: str):
    if kind == "elements":
        return load_element_text(text, name)
    return load_profile_text(text, name)


@bp.route("/files/<kind>")
@role_required("admin")
def files(kind: str):
    if kind not in KINDS:
        abort(404)
    folder = kind_dir(kind)
    paths = sorted(p for p in folder.glob("*.yaml"))
    if kind == "elements":
        lib = library()
        items = [{"name": p.name, "ok": p.name not in lib.errors, "error": lib.errors.get(p.name),
                  "info": next((f"{e.name} ({e.symbol}), version {e.definition_version}"
                                for e in lib.elements.values() if lib.files.get(e.symbol) == p), "template")}
                 for p in paths]
    else:
        profiles, errors = import_profiles()
        items = [{"name": p.name, "ok": p.name not in errors, "error": errors.get(p.name),
                  "info": next((pr.name for pr in profiles if pr.source == p.name), "template")} for p in paths]
    return render_template("admin/files.html", kind=kind, title=KINDS[kind]["title"], items=items)


@bp.route("/files/<kind>/edit", methods=["GET", "POST"])
@role_required("admin")
def file_edit(kind: str):
    if kind not in KINDS:
        abort(404)
    folder = kind_dir(kind)
    name = request.values.get("name", "")
    is_new = not name
    if name and (not re.fullmatch(r"[a-z0-9_-]+\.yaml", name) or not (folder / name).exists()):
        abort(404)
    old_text = (folder / name).read_text(encoding="utf-8") if name else ""
    if request.method == "GET":
        text = old_text
        if is_new and kind == "elements":
            text = (folder / "_template.yaml").read_text(encoding="utf-8") if (folder / "_template.yaml").exists() else ""
        return render_template("admin/file_edit.html", kind=kind, title=KINDS[kind]["title"], name=name, text=text)

    upload = request.files.get("upload")
    text = upload.read().decode("utf-8", errors="replace") if upload and upload.filename else request.form.get("text", "")
    text = text.replace("\r\n", "\n")
    try:
        obj = validate_text(kind, text, name or "new file")
    except (ElementDefinitionError, ImportProfileError) as exc:
        flash(f"Not saved: {exc}", "error")
        return render_template("admin/file_edit.html", kind=kind, title=KINDS[kind]["title"], name=name, text=text), 400

    if kind == "elements":
        target = f"{obj.symbol.lower()}.yaml"
        if is_new and (folder / target).exists():
            flash(f"{target} already exists. Edit that file instead.", "error")
            return render_template("admin/file_edit.html", kind=kind, title=KINDS[kind]["title"], name=name, text=text), 400
        if not is_new and target != name:
            flash(f"The symbol in the file ({obj.symbol}) does not match the file {name}.", "error")
            return render_template("admin/file_edit.html", kind=kind, title=KINDS[kind]["title"], name=name, text=text), 400
        if not is_new and text != old_text:
            old_version = load_element_text(old_text, name).definition_version if name not in library().errors else ""
            if obj.definition_version == old_version:
                flash("Change 'definition_version' when you change an element definition, so results stay traceable.",
                      "error")
                return render_template("admin/file_edit.html", kind=kind, title=KINDS[kind]["title"], name=name,
                                       text=text), 400
    else:
        target = name or re.sub(r"[^a-z0-9]+", "_", obj.name.lower()).strip("_")[:40] + ".yaml"
        if is_new and (folder / target).exists():
            flash(f"{target} already exists.", "error")
            return render_template("admin/file_edit.html", kind=kind, title=KINDS[kind]["title"], name=name, text=text), 400

    if not is_new and text == old_text:
        flash("No changes.", "info")
        return redirect(url_for("admin.files", kind=kind))
    hist = Path(current_app.config["HISTORY_DIR"]) / kind
    hist.mkdir(parents=True, exist_ok=True)
    if old_text:
        (hist / f"{target}.{now():%Y%m%d-%H%M%S}").write_text(old_text, encoding="utf-8")
    (folder / target).write_text(text, encoding="utf-8")
    audit(f"{kind[:-1]}_file_saved", kind, target, {"old": old_text or None, "new": text})
    db.session.commit()
    if kind == "elements":
        reload_library()
    flash(f"{target} saved.", "info")
    return redirect(url_for("admin.files", kind=kind))


# ----------------------------------------------------------------- settings

@bp.route("/settings", methods=["GET", "POST"])
@role_required("admin")
def settings():
    if request.method == "POST":
        errors = []
        try:
            k = float(request.form.get("coverage_k", "3"))
            if not 1 <= k <= 5:
                raise ValueError
        except ValueError:
            errors.append("Coverage factor must be a number between 1 and 5.")
        try:
            rho = float(request.form.get("ratio_correlation", "0"))
            if not -1 <= rho <= 1:
                raise ValueError
        except ValueError:
            errors.append("Correlation must be between -1 and 1.")
        labels = [request.form.get("sig0", "").strip(), request.form.get("sig1", "").strip()]
        footer = request.form.get("footer_statement", "").strip()
        if not all(labels) or not footer:
            errors.append("Signature labels and the final statement cannot be empty.")
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            new = {"coverage_k": k, "ratio_correlation": rho, "signature_labels": labels, "footer_statement": footer}
            old = {key: get_setting(key) for key in new}
            for key, v in new.items():
                set_setting(key, v)
            audit("settings_changed", "settings", "", {"old": old, "new": new})
            db.session.commit()
            flash("Settings saved. They apply to samples checked and reports generated from now on.", "info")
            return redirect(url_for("admin.settings"))
    values = {key: get_setting(key) for key in current_app.config["DEFAULT_SETTINGS"]}
    return render_template("admin/settings.html", v=values)


# ----------------------------------------------------------------- audit trail

@bp.route("/audit")
@role_required("admin")
def audit_log():
    q = select(AuditLog)
    a = request.args
    if a.get("user"):
        q = q.where(AuditLog.username == a["user"])
    if a.get("action"):
        q = q.where(AuditLog.action.contains(a["action"]))
    if a.get("object"):
        q = q.where(AuditLog.object_id.contains(a["object"]))
    rows = db.session.scalars(q.order_by(AuditLog.ts.desc(), AuditLog.id.desc()).limit(500)).all()
    return render_template("admin/audit.html", rows=rows, args=a)
