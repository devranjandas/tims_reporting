"""Report builder, unique IDs, JSON storage, PDF generation and recreation (URD Section 9)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request,
                   send_file, url_for)
from flask_login import current_user
from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from tims_core import report_json
from tims_core.report_json import ReportJSONError

from . import db
from .models import Report, ReportCounter, Sample, SavedHeader, User, now
from .pdf import TEMPLATE_VERSION, render_pdf
from .services import audit, get_setting, report_signing_key, report_snapshot, sample_state, state_for_version
from .views_util import role_required

bp = Blueprint("reports", __name__, url_prefix="/reports")

HEADER_FIELDS = ("division_code", "organisation", "division", "section", "address", "form_number")
CONTENTS = {"atom_percent": "Atom % ± U", "ratios": "Isotope ratios ± U"}
LAYOUTS = {"format_a": "Format A: one sample per table", "format_b": "Format B: samples side by side"}
LOGO_TYPES = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg"}
MAX_LOGO = 1024 * 1024


def reports_dir() -> Path:
    return Path(current_app.config["REPORTS_DIR"])


def get_report(pk: int) -> Report:
    r = db.session.get(Report, pk)
    if r is None:
        abort(404)
    return r


def load_json(r: Report) -> dict:
    path = reports_dir() / f"{r.file_stem}.json"
    if not path.exists():
        raise ReportJSONError(f"the JSON file {path.name} is missing from the report store")
    return report_json.loads_verified(path.read_text(encoding="utf-8"), report_signing_key())


def logo_mime(data: bytes) -> str | None:
    for magic, mime in LOGO_TYPES.items():
        if data.startswith(magic):
            return mime
    return None


def stored_logo(data: dict) -> tuple[tuple[bytes, str] | None, str | None]:
    """Logo saved with a report, checked against the hash in the JSON."""
    h = data.get("header", {})
    if not h.get("logo_file"):
        return None, None
    path = reports_dir() / Path(h["logo_file"]).name
    if not path.exists():
        return None, "The logo file of this report is missing; the PDF is drawn without it."
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != h.get("logo_sha256"):
        return None, "The logo file does not match the report; the PDF is drawn without it."
    return (raw, logo_mime(raw) or "image/png"), None


# ----------------------------------------------------------------- list

@bp.route("/")
@role_required("viewer", "analyst")
def index():
    reports = db.session.scalars(select(Report).order_by(Report.created_at.desc(), Report.id.desc())).all()
    return render_template("reports/list.html", reports=reports, display_id=report_json.display_id)


@bp.route("/<int:pk>/pdf")
@role_required("viewer", "analyst")
def pdf(pk: int):
    r = get_report(pk)
    path = reports_dir() / f"{r.file_stem}.pdf"
    if not path.exists():
        flash("The PDF file is missing. Use 'Recreate' to draw it again from the JSON.", "error")
        return redirect(url_for("reports.index"))
    return send_file(path, mimetype="application/pdf", download_name=f"{r.file_stem}.pdf")


@bp.route("/<int:pk>/json")
@role_required("viewer", "analyst")
def json_file(pk: int):
    r = get_report(pk)
    path = reports_dir() / f"{r.file_stem}.json"
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="application/json", as_attachment=True, download_name=f"{r.file_stem}.json")


# ----------------------------------------------------------------- recreate

def compare_with_database(data: dict) -> tuple[list[str], list[str]]:
    """Differences between the reported values and the database now (FR-REP-12)."""
    diffs, missing = [], []
    for snap in data["samples"]:
        s = db.session.scalars(select(Sample).where(Sample.element == snap["element"],
                                                    Sample.sample_id == snap["sample_id"])).first()
        if s is None:
            missing.append(f"{snap['element']} sample {snap['sample_id']} is not in the database.")
            continue
        state = state_for_version(s, snap["record_version"])
        if state is None:
            diffs.append(f"{snap['sample_id']}: version {snap['record_version']} is no longer stored "
                         f"(current version {s.version}, {s.status}).")
            continue
        if s.version != snap["record_version"]:
            diffs.append(f"{snap['sample_id']}: a newer version ({s.version}, {s.status}) exists since this report.")
        for d in report_json.diff_snapshots(snap, report_snapshot(state)):
            diffs.append(f"{snap['sample_id']}: {d}")
    return diffs, missing


@bp.route("/<int:pk>/recreate")
@role_required("viewer", "analyst")
def recreate(pk: int):
    r = get_report(pk)
    try:
        data = load_json(r)
    except ReportJSONError as exc:
        return render_template("reports/recreate.html", r=r, error=str(exc), diffs=[], missing=[], warning=None)
    diffs, missing = compare_with_database(data)
    _, warning = stored_logo(data)
    return render_template("reports/recreate.html", r=r, data=data, error=None, diffs=diffs, missing=missing,
                           warning=warning, display_id=report_json.display_id)


@bp.route("/<int:pk>/recreate.pdf")
@role_required("viewer", "analyst")
def recreate_pdf(pk: int):
    r = get_report(pk)
    try:
        data = load_json(r)
    except ReportJSONError as exc:
        flash(f"Cannot recreate: {exc}", "error")
        return redirect(url_for("reports.recreate", pk=pk))
    diffs, missing = compare_with_database(data)
    if missing:
        flash("Cannot recreate: " + " ".join(missing), "error")
        return redirect(url_for("reports.recreate", pk=pk))
    logo, _ = stored_logo(data)
    pdf_bytes = render_pdf(data, logo)
    path = reports_dir() / f"{r.file_stem}.pdf"
    if not path.exists():
        path.write_bytes(pdf_bytes)
    audit("report_recreated", "report", report_json.display_id(r.report_id, r.revision), {"differences": diffs})
    db.session.commit()
    return Response(pdf_bytes, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{r.file_stem}.pdf"'})


@bp.route("/upload", methods=["POST"])
@role_required("viewer", "analyst")
def upload():
    f = request.files.get("report_json")
    text = f.read().decode("utf-8", errors="replace") if f and f.filename else request.form.get("json_text", "")
    try:
        data = report_json.loads_verified(text, report_signing_key())
    except ReportJSONError as exc:
        flash(f"The report file was refused: {exc}", "error")
        return redirect(url_for("reports.index"))
    diffs, missing = compare_with_database(data)
    _, warning = stored_logo(data)
    if request.form.get("render") == "1":
        if missing:
            flash("Cannot recreate: " + " ".join(missing), "error")
            return redirect(url_for("reports.index"))
        logo, _ = stored_logo(data)
        audit("report_recreated", "report", report_json.display_id(data["report_id"], data["revision"]),
              {"differences": diffs, "via": "uploaded JSON"})
        db.session.commit()
        stem = report_json.file_stem(data["report_id"], data["revision"])
        return Response(render_pdf(data, logo), mimetype="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{stem}.pdf"'})
    return render_template("reports/recreate.html", r=None, data=data, error=None, diffs=diffs, missing=missing,
                           warning=warning, json_text=text, display_id=report_json.display_id)


# ----------------------------------------------------------------- saved headers

def can_manage_header(h: SavedHeader) -> bool:
    return h.created_by_id == current_user.id or current_user.can("admin")


@bp.route("/headers")
@role_required("analyst")
def headers():
    rows = db.session.scalars(select(SavedHeader).order_by(SavedHeader.name)).all()
    return render_template("reports/headers.html", headers=rows, can_manage=can_manage_header)


@bp.route("/headers/<int:pk>", methods=["GET", "POST"])
@role_required("analyst")
def header_edit(pk: int):
    h = db.session.get(SavedHeader, pk) or abort(404)
    if not can_manage_header(h):
        abort(403)
    if request.method == "POST":
        if request.form.get("action") == "delete":
            audit("header_deleted", "header", h.name)
            db.session.delete(h)
            db.session.commit()
            flash("Saved header deleted. Reports already generated are not affected.", "info")
            return redirect(url_for("reports.headers"))
        fields = {k: request.form.get(k, "").strip() for k in HEADER_FIELDS}
        fields["division_code"] = fields["division_code"].upper()
        errors = header_errors(fields)
        name = request.form.get("name", "").strip()
        if not name:
            errors.append("Give the header a name.")
        other = db.session.scalars(select(SavedHeader).where(SavedHeader.name == name, SavedHeader.id != h.id)).first()
        if other:
            errors.append("Another saved header already has that name.")
        logo, logo_err = read_logo_upload()
        if logo_err:
            errors.append(logo_err)
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            h.name = name
            for k, v in fields.items():
                setattr(h, k, v)
            if logo:
                h.logo, h.logo_mime = logo
            if request.form.get("remove_logo"):
                h.logo, h.logo_mime = None, ""
            audit("header_edited", "header", h.name, fields)
            db.session.commit()
            flash("Saved header updated. Reports already generated are not affected.", "info")
            return redirect(url_for("reports.headers"))
    return render_template("reports/header_edit.html", h=h)


@bp.route("/headers/<int:pk>/logo")
@role_required("analyst")
def header_logo(pk: int):
    h = db.session.get(SavedHeader, pk)
    if not h or not h.logo:
        abort(404)
    return Response(h.logo, mimetype=h.logo_mime)


# ----------------------------------------------------------------- new report / revision

def header_errors(h: dict) -> list[str]:
    errors = []
    if not re.fullmatch(r"[A-Z]{2,6}", h.get("division_code", "")):
        errors.append("Division code must be 2 to 6 letters (A–Z); it is used in the Report ID.")
    if not h.get("organisation"):
        errors.append("Organisation name is required.")
    if not h.get("division"):
        errors.append("Division name is required.")
    return errors


def read_logo_upload() -> tuple[tuple[bytes, str] | None, str | None]:
    f = request.files.get("logo")
    if not f or not f.filename:
        return None, None
    raw = f.read(MAX_LOGO + 1)
    if len(raw) > MAX_LOGO:
        return None, "The logo must be smaller than 1 MB."
    mime = logo_mime(raw)
    if not mime:
        return None, "The logo must be a PNG or JPEG image."
    return (raw, mime), None


def selectable_samples() -> list[Sample]:
    return db.session.scalars(select(Sample).order_by(Sample.analysis_date.desc(), Sample.id.desc()).limit(5000)).all()


def form_defaults() -> dict:
    labels = get_setting("signature_labels")
    return {
        "header": {k: "" for k in HEADER_FIELDS},
        "content": "atom_percent", "layout": "format_a", "remarks": "",
        "signatories": [
            {"label": labels[0], "name": current_user.full_name, "designation": current_user.designation},
            {"label": labels[1], "name": "", "designation": ""},
        ],
        "selected": [], "saved_header_id": "", "revision_reason": "",
    }


def read_report_form() -> dict:
    labels = get_setting("signature_labels")
    f = request.form
    header = {k: f.get(k, "").strip() for k in HEADER_FIELDS}
    header["division_code"] = header["division_code"].upper()
    return {
        "header": header,
        "content": f.get("content", "atom_percent"),
        "layout": f.get("layout", "format_a"),
        "remarks": f.get("remarks", "").strip(),
        "signatories": [
            {"label": labels[i], "name": f.get(f"sig{i}_name", "").strip(),
             "designation": f.get(f"sig{i}_designation", "").strip()} for i in (0, 1)
        ],
        "selected": [int(x) for x in f.getlist("sample") if x.isdigit()],
        "saved_header_id": f.get("saved_header_id", ""),
        "save_header_name": f.get("save_header_name", "").strip(),
        "revision_reason": f.get("revision_reason", "").strip(),
        "use_previous_logo": f.get("use_previous_logo", ""),
    }


def render_report_form(form: dict, revise_of: Report | None = None, errors=None, status=200):
    users = db.session.scalars(select(User).where(User.active.is_(True)).order_by(User.full_name)).all()
    saved = db.session.scalars(select(SavedHeader).order_by(SavedHeader.name)).all()
    saved_js = {str(h.id): {**h.fields(), "has_logo": bool(h.logo)} for h in saved}
    return render_template("reports/new.html", form=form, samples=selectable_samples(), users=users,
                           saved=saved, saved_js=saved_js, contents=CONTENTS, layouts=LAYOUTS,
                           revise_of=revise_of, errors=errors or [],
                           display_id=report_json.display_id), status


@bp.route("/new", methods=["GET", "POST"])
@role_required("analyst")
def new():
    revise_of = None
    if request.values.get("revise_of", "").isdigit():
        revise_of = get_report(int(request.values["revise_of"]))
    if request.method == "GET":
        form = form_defaults()
        if revise_of:
            form = prefill_from_report(revise_of, form)
        elif request.args.getlist("sample"):
            form["selected"] = [int(x) for x in request.args.getlist("sample") if x.isdigit()]
        return render_report_form(form, revise_of)

    form = read_report_form()
    errors = header_errors(form["header"])
    if form["content"] not in CONTENTS:
        errors.append("Choose what the table shows.")
    if form["layout"] not in LAYOUTS:
        errors.append("Choose a layout.")
    samples = [db.session.get(Sample, pk) for pk in form["selected"]]
    samples = [s for s in samples if s is not None]
    if not samples:
        errors.append("Select at least one sample.")
    if form["layout"] == "format_b" and len({s.element for s in samples}) > 1:
        errors.append("Format B needs samples of one element. Use Format A for mixed elements.")
    no_results = [s.sample_id for s in samples if not s.results]
    if no_results:
        errors.append("These samples have no results yet (fix their entry errors first): " + ", ".join(no_results))
    for sig in form["signatories"]:
        if not sig["name"]:
            errors.append(f"Enter the name for '{sig['label']}'.")
    if revise_of and not form["revision_reason"]:
        errors.append("Give the reason for this revision.")
    logo, logo_err = read_logo_upload()
    if logo_err:
        errors.append(logo_err)
    if logo is None and form["saved_header_id"].isdigit() and not request.form.get("no_logo"):
        h = db.session.get(SavedHeader, int(form["saved_header_id"]))
        if h and h.logo:
            logo = (h.logo, h.logo_mime)
    if logo is None and revise_of and form["use_previous_logo"] and not request.form.get("no_logo"):
        try:
            logo, _ = stored_logo(load_json(revise_of))
        except ReportJSONError:
            logo = None
    if form["save_header_name"]:
        existing = db.session.scalars(select(SavedHeader).where(SavedHeader.name == form["save_header_name"])).first()
        if existing and not can_manage_header(existing):
            errors.append("A saved header with that name belongs to another user. Choose another name.")
    if errors:
        return render_report_form(form, revise_of, errors, 400)

    action = request.form.get("action", "preview")
    created = now()
    snapshots = [report_snapshot(sample_state(s)) for s in samples]
    base = {
        "content": form["content"], "layout": form["layout"], "header": dict(form["header"]),
        "signatories": form["signatories"], "remarks": form["remarks"],
        "footer_statement": get_setting("footer_statement"),
        "draft": any(s["status"] != "Approved" for s in snapshots), "samples": snapshots,
    }
    if action == "preview":
        data = {"schema_version": 1, "report_id": "PREVIEW", "revision": 0,
                "created_at": created.astimezone().isoformat(timespec="seconds"),
                "software_version": current_app.config["SOFTWARE_VERSION"], **base}
        return Response(render_pdf(data, logo, preview=True), mimetype="application/pdf",
                        headers={"Content-Disposition": 'inline; filename="preview.pdf"'})

    if form["save_header_name"]:
        save_header(form, logo)
    r = generate(base, logo, created, revise_of, form["revision_reason"])
    flash(f"Report {report_json.display_id(r.report_id, r.revision)} generated"
          + (" as a DRAFT (it contains unapproved samples)." if r.draft else "."), "info")
    return redirect(url_for("reports.index", new=r.id))


def save_header(form: dict, logo) -> None:
    name = form["save_header_name"]
    h = db.session.scalars(select(SavedHeader).where(SavedHeader.name == name)).first()
    if h is None:
        h = SavedHeader(name=name, created_by_id=current_user.id)
        db.session.add(h)
    for k, v in form["header"].items():
        setattr(h, k, v)
    if logo:
        h.logo, h.logo_mime = logo
    audit("header_saved", "header", name, form["header"])


def next_number(year: int) -> int:
    """Atomic yearly counter: the UPDATE takes SQLite's write lock, so two users never get the same number."""
    db.session.execute(sqlite_insert(ReportCounter).values(year=year, last_seq=0).on_conflict_do_nothing())
    db.session.execute(update(ReportCounter).where(ReportCounter.year == year)
                       .values(last_seq=ReportCounter.last_seq + 1))
    return db.session.scalar(select(ReportCounter.last_seq).where(ReportCounter.year == year))


def generate(base: dict, logo, created: datetime, revise_of: Report | None, reason: str) -> Report:
    if revise_of:
        report_id = revise_of.report_id
        revision = (db.session.scalar(select(func.max(Report.revision)).where(Report.report_id == report_id)) or 0) + 1
        prev = db.session.scalars(select(Report).where(Report.report_id == report_id, Report.revision == revision - 1)).first()
        supersedes = report_json.display_id(report_id, prev.revision if prev else revise_of.revision)
    else:
        seq = next_number(created.year)
        report_id = f"TIMS/{base['header']['division_code']}/{created.year}/{seq:04d}"
        revision, supersedes = 0, None
    stem = report_json.file_stem(report_id, revision)
    header = dict(base["header"])
    logo_path = None
    if logo:
        ext = "png" if logo[1] == "image/png" else "jpg"
        logo_path = reports_dir() / f"{stem}_logo.{ext}"
        header["logo_file"] = logo_path.name
        header["logo_sha256"] = hashlib.sha256(logo[0]).hexdigest()
    data = {
        "schema_version": report_json.SCHEMA_VERSION,
        "report_id": report_id,
        "revision": revision,
        "supersedes": supersedes,
        "revision_reason": reason or None,
        "created_at": created.astimezone().isoformat(timespec="seconds"),
        "created_by": current_user.username,
        "created_by_name": current_user.full_name,
        "software_version": current_app.config["SOFTWARE_VERSION"],
        "template": {"name": base["layout"], "version": TEMPLATE_VERSION},
        **{k: v for k, v in base.items() if k != "header"},
        "header": header,
    }
    data = report_json.seal(data, report_signing_key())
    pdf_bytes = render_pdf(data, logo)
    json_path = reports_dir() / f"{stem}.json"
    pdf_path = reports_dir() / f"{stem}.pdf"
    if json_path.exists():
        raise RuntimeError(f"report file {json_path.name} already exists")
    if logo_path:
        logo_path.write_bytes(logo[0])
    json_path.write_text(report_json.dumps(data), encoding="utf-8")
    pdf_path.write_bytes(pdf_bytes)
    r = Report(report_id=report_id, revision=revision, draft=data["draft"], file_stem=stem, content=data["content"],
               layout=data["layout"], revision_reason=reason or "", created_by_id=current_user.id, created_at=created,
               sample_list=", ".join(f"{s['sample_id']} ({s['element']})" for s in data["samples"]))
    db.session.add(r)
    audit("report_generated", "report", report_json.display_id(report_id, revision),
          {"samples": [(s["sample_id"], s["record_version"]) for s in data["samples"]],
           "draft": data["draft"], "checksum": data[report_json.CHECKSUM_FIELD], "reason": reason or None})
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        for p in (json_path, pdf_path, logo_path):
            if p and p.exists():
                p.unlink()
        raise
    return r


def prefill_from_report(r: Report, form: dict) -> dict:
    try:
        data = load_json(r)
    except ReportJSONError as exc:
        flash(f"Could not read the report file: {exc}", "error")
        return form
    form["header"] = {k: data["header"].get(k, "") for k in HEADER_FIELDS}
    form["content"], form["layout"] = data["content"], data["layout"]
    form["remarks"] = data.get("remarks", "")
    sigs = data.get("signatories") or []
    for i in (0, 1):
        if i < len(sigs):
            form["signatories"][i].update(name=sigs[i].get("name", ""), designation=sigs[i].get("designation", ""))
    ids = []
    for snap in data["samples"]:
        s = db.session.scalars(select(Sample).where(Sample.element == snap["element"],
                                                    Sample.sample_id == snap["sample_id"])).first()
        if s:
            ids.append(s.id)
    form["selected"] = ids
    form["use_previous_logo"] = "1" if data["header"].get("logo_file") else ""
    return form


@bp.app_template_filter("tojson_compact")
def _tojson_compact(v):
    return json.dumps(v, separators=(",", ":"))
