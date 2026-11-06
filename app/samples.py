"""Sample entry, import, checks, review workflow and export (URD 6.1, 6.2, 6.5, 6.6, 6.7)."""

from __future__ import annotations

import csv
import io
from datetime import date

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_, select

from tims_core import parsing, qc
from tims_core.qc import deviation_pct

from . import db
from .models import AuditLog, Sample, SampleVersion, User, now
from .services import (active_baseline, apply_suggestion, audit, batch_reference, element, evaluate,
                       freeze_version, import_profiles, library, norm_label, open_problems, parse_date,
                       ratio_values, touch)
from .views_util import role_required

bp = Blueprint("samples", __name__, url_prefix="/samples")

META_FIELDS = ("sample_id", "sample_type", "analysis_date", "instrument", "analyst_name", "n_measurements",
               "run_id", "batch_ref", "enriched_isotope", "reference_sample", "remarks", "rsd_unit")
SPARE_ROWS = 2


# ----------------------------------------------------------------- helpers

def get_sample(pk: int) -> Sample:
    s = db.session.get(Sample, pk)
    if s is None:
        abort(404)
    return s


def can_edit(s: Sample) -> bool:
    return s.editable and (s.created_by_id == current_user.id or current_user.can("reviewer"))


def can_review(s: Sample) -> bool:
    return current_user.can("reviewer") and s.created_by_id != current_user.id


def read_upload_text(field: str) -> str:
    f = request.files.get(field)
    if not f or not f.filename:
        return ""
    raw = f.read()
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def blank_form(symbol: str) -> dict:
    return {"sample_id": "", "sample_type": "Natural", "analysis_date": date.today().isoformat(), "instrument": "",
            "analyst_name": current_user.full_name, "n_measurements": "30", "run_id": "", "batch_ref": "",
            "enriched_isotope": "", "reference_sample": "", "remarks": "", "rsd_unit": "ppm", "element": symbol}


def default_entries(symbol: str) -> list[dict]:
    return [{"label": lbl, "value": "", "rsd": ""} for lbl in element(symbol).expected_labels()]


def form_from_sample(s: Sample) -> dict:
    d = {k: ("" if getattr(s, k) is None else str(getattr(s, k))) for k in META_FIELDS}
    d["analysis_date"] = s.analysis_date.isoformat() if s.analysis_date else ""
    d["element"] = s.element
    return d


def read_form() -> tuple[dict, list[dict]]:
    form = {k: request.form.get(k, "").strip() for k in META_FIELDS}
    form["remarks"] = request.form.get("remarks", "")
    form["element"] = request.form.get("element", "")
    labels, values, rsds = (request.form.getlist(n) for n in ("label", "value", "rsd"))
    entries = []
    for lbl, v, r in zip(labels, values, rsds):
        if lbl.strip() or v.strip() or r.strip():
            entries.append({"label": lbl.strip(), "value": v.strip(), "rsd": r.strip()})
    return form, entries


def validate_meta(form: dict, existing: Sample | None = None) -> list[str]:
    errors = []
    if form["element"] not in library():
        errors.append("Choose an element.")
        return errors
    if not form["sample_id"]:
        errors.append("Sample ID is required.")
    if form["sample_type"] not in qc.SAMPLE_TYPES:
        errors.append("Choose a sample type.")
    if not parse_date(form["analysis_date"]):
        errors.append("Enter the date of analysis (YYYY-MM-DD).")
    try:
        if int(form["n_measurements"]) < 1:
            raise ValueError
    except ValueError:
        errors.append("Number of measurements must be a whole number of at least 1.")
    if form["rsd_unit"] not in ("ppm", "percent", "absolute"):
        errors.append("Choose the RSD unit.")
    if form["enriched_isotope"]:
        try:
            if int(form["enriched_isotope"]) not in element(form["element"]).isotopes:
                raise ValueError
        except ValueError:
            errors.append("The enriched isotope must be an isotope of the element.")
    if form["sample_type"] == "Enriched" and not form["enriched_isotope"]:
        errors.append("State the enriched isotope for an enriched sample.")
    dup = db.session.scalars(select(Sample).where(Sample.element == form["element"],
                                                  Sample.sample_id == form["sample_id"])).first()
    if dup and (existing is None or dup.id != existing.id):
        errors.append(f"Sample ID {form['sample_id']} already exists for {form['element']}.")
    return errors


def apply_form(s: Sample, form: dict, entries: list[dict]) -> None:
    s.sample_id = form["sample_id"]
    s.element = form["element"]
    s.sample_type = form["sample_type"]
    s.analysis_date = parse_date(form["analysis_date"])
    s.instrument = form["instrument"]
    s.analyst_name = form["analyst_name"]
    s.n_measurements = int(form["n_measurements"])
    s.run_id = form["run_id"]
    s.batch_ref = form["batch_ref"]
    s.enriched_isotope = int(form["enriched_isotope"]) if form["enriched_isotope"] else None
    s.reference_sample = form["reference_sample"]
    s.remarks = form["remarks"]
    s.rsd_unit = form["rsd_unit"]
    s.entries = entries


def parse_into_form(form: dict, entries: list[dict]) -> tuple[dict, list[dict], parsing.ParseResult | None]:
    text = request.form.get("paste", "") or read_upload_text("import_file")
    if not text.strip():
        flash("Paste the instrument text or choose a file first.", "error")
        return form, entries, None
    profiles, _ = import_profiles()
    choice = request.form.get("profile", "auto")
    chosen = [p for p in profiles if p.name == choice]
    result = parsing.parse(text, chosen[0]) if chosen else parsing.parse_auto(text, profiles)
    if not result.blocks or not result.blocks[0].rows:
        flash("No ratios could be read from the text. Check the import format.", "error")
        return form, entries, result
    block = result.blocks[0]
    if block.element and block.element in library():
        form["element"] = block.element
    elif block.element:
        flash(f"Element '{block.element}' in the text is not in the element library.", "error")
    if block.sample and not form.get("sample_id"):
        form["sample_id"] = block.sample
    form["rsd_unit"] = result.rsd_unit
    entries = [{"label": r.label_raw, "value": r.mean_raw, "rsd": r.rsd_raw} for r in block.rows]
    msg = f"Read {len(block.rows)} ratios using '{result.profile}'. Check the values, then save."
    if len(result.blocks) > 1:
        msg += f" The text holds {len(result.blocks)} samples: only the first was used. Use Bulk import for all."
    flash(msg, "info")
    return form, entries, result


def render_form(mode: str, form: dict, entries: list[dict], sample: Sample | None = None, parsed=None, errors=None):
    symbol = form.get("element") or (library().symbols() or [""])[0]
    el = element(symbol) if symbol in library() else None
    rows = list(entries) + [{"label": "", "value": "", "rsd": ""} for _ in range(SPARE_ROWS)]
    profiles, _ = import_profiles()
    return render_template("samples/form.html", mode=mode, form=form, rows=rows, el=el, sample=sample,
                           elements=[library().get(s) for s in library().symbols()], profiles=profiles,
                           parsed=parsed, errors=errors or [], sample_types=qc.SAMPLE_TYPES)


# ----------------------------------------------------------------- list / export

def filtered_query():
    q = select(Sample)
    a = request.args
    if a.get("q"):
        like = f"%{a['q'].strip()}%"
        q = q.where(or_(Sample.sample_id.ilike(like), Sample.batch_ref.ilike(like), Sample.remarks.ilike(like)))
    if a.get("element"):
        q = q.where(Sample.element == a["element"])
    if a.get("type"):
        q = q.where(Sample.sample_type == a["type"])
    if a.get("status"):
        q = q.where(Sample.status == a["status"])
    if a.get("analyst"):
        q = q.where(Sample.analyst_name.ilike(f"%{a['analyst']}%"))
    if (d := parse_date(a.get("from"))):
        q = q.where(Sample.analysis_date >= d)
    if (d := parse_date(a.get("to"))):
        q = q.where(Sample.analysis_date <= d)
    return q.order_by(Sample.analysis_date.desc(), Sample.id.desc())


@bp.route("/")
@role_required("analyst")
def index():
    samples = db.session.scalars(filtered_query().limit(1000)).all()
    return render_template("samples/list.html", samples=samples, elements=library().symbols(),
                           sample_types=qc.SAMPLE_TYPES, args=request.args)


@bp.route("/export.csv")
@role_required("analyst")
def export_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["sample_id", "element", "sample_type", "status", "version", "analysis_date", "instrument",
                "quantity", "label", "value", "rsd_ppm", "U_3SD", "assumed_natural"])
    for s in db.session.scalars(filtered_query()).all():
        base = [s.sample_id, s.element, s.sample_type, s.status, s.version,
                s.analysis_date.isoformat() if s.analysis_date else "", s.instrument]
        res = s.results or {}
        for r in res.get("ratios", []):
            w.writerow(base + ["ratio", r["label"], repr(r["value"]), repr(r["rsd_ppm"]), repr(r["U"]), ""])
        for a in res.get("abundances", []):
            w.writerow(base + ["atom_percent", f"{a['isotope']}{s.element}", repr(a["atom_pct"]), "", repr(a["U"]),
                               "yes" if a["assumed"] else ""])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=tims_samples.csv"})


# ----------------------------------------------------------------- create / edit

@bp.route("/new", methods=["GET", "POST"])
@role_required("analyst")
def new():
    if not library().symbols():
        flash("No elements are defined yet. An administrator must add element definitions.", "error")
        return redirect(url_for("main.index"))
    if request.method == "GET":
        symbol = request.args.get("element") or library().symbols()[0]
        if symbol not in library():
            abort(404)
        return render_form("new", blank_form(symbol), default_entries(symbol))

    form, entries = read_form()
    action = request.form.get("action", "save")
    if action == "parse":
        form, entries, parsed = parse_into_form(form, entries)
        return render_form("new", form, entries, parsed=parsed)
    errors = validate_meta(form)
    if errors:
        return render_form("new", form, entries, errors=errors), 400
    s = Sample(created_by_id=current_user.id, status="Draft", version=1)
    apply_form(s, form, entries)
    db.session.add(s)
    evaluate(s)
    db.session.flush()
    audit("sample_created", "sample", s.id, {"sample_id": s.sample_id, "element": s.element, "entries": entries})
    db.session.commit()
    flash(f"Sample {s.sample_id} saved as a draft. Review the checks below.", "info")
    return redirect(url_for("samples.view", pk=s.id))


@bp.route("/<int:pk>/edit", methods=["GET", "POST"])
@role_required("analyst")
def edit(pk: int):
    s = get_sample(pk)
    if not can_edit(s):
        flash("This sample cannot be edited in its current state.", "error")
        return redirect(url_for("samples.view", pk=pk))
    if request.method == "GET":
        return render_form("edit", form_from_sample(s), s.entries, sample=s)

    form, entries = read_form()
    form["element"] = s.element  # the element of a saved sample cannot change
    action = request.form.get("action", "save")
    if action == "parse":
        form, entries, parsed = parse_into_form(form, entries)
        form["element"] = s.element
        return render_form("edit", form, entries, sample=s, parsed=parsed)
    if str(s.lock_version) != request.form.get("lock_version"):
        flash("Someone else saved this sample after you opened it. Your changes were not saved. "
              "Reload the sample to see the latest version.", "error")
        return render_form("edit", form, entries, sample=s), 409
    errors = validate_meta(form, existing=s)
    if errors:
        return render_form("edit", form, entries, sample=s, errors=errors), 400
    before = {"entries": s.entries, "sample_id": s.sample_id, "sample_type": s.sample_type}
    apply_form(s, form, entries)
    if s.status == "Rejected":
        s.status = "Draft"
    evaluate(s)
    touch(s)
    audit("sample_edited", "sample", s.id, {"before": before, "after": {"entries": entries, "sample_id": s.sample_id,
                                                                         "sample_type": s.sample_type}})
    db.session.commit()
    flash("Changes saved.", "info")
    return redirect(url_for("samples.view", pk=s.id))


# ----------------------------------------------------------------- view

def _row_status(findings: list[dict], target: str) -> str:
    sev = [f["severity"] for f in findings if f.get("target") in (target, target + ":rsd") and f["severity"] != "INFO"]
    if "ERROR" in sev:
        return "ERROR"
    if "WARNING" in sev:
        return "WARNING"
    return "OK"


@bp.route("/<int:pk>")
@role_required("analyst")
def view(pk: int):
    s = get_sample(pk)
    el = element(s.element) if s.element in library() else None
    findings = s.findings or []
    res = s.results or {}
    baseline = active_baseline(s.element) if s.sample_type in qc.NATURAL_TYPES else None
    batch_vals = {}
    if s.sample_type == "Enriched" and (s.reference_info or {}).get("batch_sample"):
        ref, _ = batch_reference(s)
        batch_vals = ratio_values(ref) if ref else {}
    ratio_rows = []
    for r in res.get("ratios", []):
        num, den = (int(x) for x in r["label"].split("/"))
        nat = el.natural_ratio(num, den) if el else None
        z = None
        if baseline and r["label"] in baseline.stats and baseline.stats[r["label"]]["sd"] > 0:
            st = baseline.stats[r["label"]]
            z = (r["value"] - st["mean"]) / st["sd"]
        ratio_rows.append({**r, "natural": nat, "dev_nat": deviation_pct(r["value"], nat) if nat else None, "z": z,
                           "dev_batch": deviation_pct(r["value"], batch_vals[r["label"]]) if r["label"] in batch_vals else None,
                           "status": _row_status(findings, r["label"])})
    abundance_rows = []
    for a in res.get("abundances", []):
        nat = el.natural_abundance(a["isotope"]) if el else None
        abundance_rows.append({**a, "natural": nat, "dev_nat": deviation_pct(a["atom_pct"], nat) if nat else None})
    return render_template("samples/view.html", s=s, el=el, findings=findings, ratio_rows=ratio_rows,
                           abundance_rows=abundance_rows, problems=open_problems(s), baseline=baseline,
                           can_edit=can_edit(s), can_review=can_review(s),
                           is_owner=s.created_by_id == current_user.id)


@bp.route("/<int:pk>/history")
@role_required("analyst")
def history(pk: int):
    s = get_sample(pk)
    versions = db.session.scalars(select(SampleVersion).where(SampleVersion.sample_pk == s.id)
                                  .order_by(SampleVersion.version)).all()
    logs = db.session.scalars(select(AuditLog).where(AuditLog.object_type == "sample", AuditLog.object_id == str(s.id))
                              .order_by(AuditLog.ts.desc(), AuditLog.id.desc())).all()
    return render_template("samples/history.html", s=s, versions=versions, logs=logs)


# ----------------------------------------------------------------- actions

@bp.route("/<int:pk>/suggestion", methods=["POST"])
@role_required("analyst")
def accept_suggestion(pk: int):
    s = get_sample(pk)
    if not can_edit(s):
        abort(403)
    try:
        what = apply_suggestion(s, request.form.get("key", ""))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("samples.view", pk=pk))
    if s.status == "Rejected":
        s.status = "Draft"
    evaluate(s)
    touch(s)
    audit("suggestion_accepted", "sample", s.id, {"change": what, "finding": request.form.get("key")})
    db.session.commit()
    flash(f"Suggestion applied: {what}.", "info")
    return redirect(url_for("samples.view", pk=pk))


@bp.route("/<int:pk>/justify", methods=["POST"])
@role_required("analyst")
def justify(pk: int):
    s = get_sample(pk)
    if s.status == "Approved" or not (s.created_by_id == current_user.id or current_user.can("reviewer")):
        abort(403)
    changed = []
    findings = [dict(f) for f in s.findings or []]
    for f in findings:
        if f["severity"] != "WARNING":
            continue
        text = request.form.get(f"j_{f['key']}")
        if text is None:
            continue
        text = text.strip()
        if text != (f.get("justification") or ""):
            f["justification"] = text
            f["justified_by"] = current_user.full_name if text else ""
            changed.append({"finding": f["key"], "justification": text})
    if changed:
        s.findings = findings
        touch(s)
        audit("warnings_justified", "sample", s.id, {"changes": changed})
        db.session.commit()
        flash("Justifications saved.", "info")
    return redirect(url_for("samples.view", pk=pk))


@bp.route("/<int:pk>/override", methods=["POST"])
@role_required("reviewer")
def override(pk: int):
    s = get_sample(pk)
    if s.status == "Approved" or not can_review(s):
        abort(403)
    key = request.form.get("key", "")
    comment = request.form.get("comment", "").strip()
    findings = [dict(f) for f in s.findings or []]
    f = next((f for f in findings if f["key"] == key and f["severity"] == "ERROR"), None)
    if f is None:
        abort(404)
    if f["code"] in ("QC-01", "QC-32") or (f["code"] == "QC-03" and not s.results):
        flash("This error stops the calculation and cannot be overridden. The entry must be corrected.", "error")
        return redirect(url_for("samples.view", pk=pk))
    f["override_comment"] = comment
    f["overridden_by"] = current_user.full_name if comment else ""
    s.findings = findings
    touch(s)
    audit("error_overridden" if comment else "override_removed", "sample", s.id, {"finding": key, "comment": comment})
    db.session.commit()
    flash("Override saved." if comment else "Override removed.", "info")
    return redirect(url_for("samples.view", pk=pk))


@bp.route("/<int:pk>/submit", methods=["POST"])
@role_required("analyst")
def submit(pk: int):
    s = get_sample(pk)
    if not can_edit(s):
        abort(403)
    evaluate(s)  # checks run again before submission
    problems = open_problems(s)
    if problems:
        db.session.commit()
        flash("The sample cannot be submitted yet: " + " | ".join(problems), "error")
        return redirect(url_for("samples.view", pk=pk))
    s.status = "Submitted"
    s.submitted_at = now()
    touch(s)
    audit("sample_submitted", "sample", s.id)
    db.session.commit()
    flash("Sample submitted for review.", "info")
    return redirect(url_for("samples.view", pk=pk))


@bp.route("/<int:pk>/approve", methods=["POST"])
@role_required("reviewer")
def approve(pk: int):
    s = get_sample(pk)
    if s.status != "Submitted" or not can_review(s):
        abort(403)
    evaluate(s)
    problems = open_problems(s)
    if problems:
        db.session.commit()
        flash("The sample cannot be approved: " + " | ".join(problems), "error")
        return redirect(url_for("samples.view", pk=pk))
    s.status = "Approved"
    s.reviewed_by_id = current_user.id
    s.reviewed_at = now()
    s.review_comment = request.form.get("comment", "").strip()
    touch(s)
    db.session.flush()
    freeze_version(s, "approved")
    audit("sample_approved", "sample", s.id, {"version": s.version, "comment": s.review_comment})
    db.session.commit()
    flash(f"Sample {s.sample_id} approved (version {s.version}).", "info")
    return redirect(url_for("samples.view", pk=pk))


@bp.route("/<int:pk>/reject", methods=["POST"])
@role_required("reviewer")
def reject(pk: int):
    s = get_sample(pk)
    if s.status != "Submitted" or not can_review(s):
        abort(403)
    comment = request.form.get("comment", "").strip()
    if not comment:
        flash("Give a reason for rejecting the sample.", "error")
        return redirect(url_for("samples.view", pk=pk))
    s.status = "Rejected"
    s.reviewed_by_id = current_user.id
    s.reviewed_at = now()
    s.review_comment = comment
    touch(s)
    audit("sample_rejected", "sample", s.id, {"comment": comment})
    db.session.commit()
    flash("Sample rejected and returned to the analyst.", "info")
    return redirect(url_for("samples.view", pk=pk))


@bp.route("/<int:pk>/revise", methods=["POST"])
@role_required("analyst")
def revise(pk: int):
    s = get_sample(pk)
    if s.status != "Approved":
        abort(403)
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Give a reason for revising an approved sample.", "error")
        return redirect(url_for("samples.view", pk=pk))
    freeze_version(s, "approved")  # make sure the approved copy is kept
    s.version += 1
    s.status = "Draft"
    s.reviewed_by_id = None
    s.reviewed_at = None
    s.review_comment = ""
    s.findings = [{**f, "justification": "", "justified_by": "", "override_comment": "", "overridden_by": ""}
                  for f in s.findings or []]
    touch(s)
    audit("sample_revision_started", "sample", s.id, {"new_version": s.version, "reason": reason})
    db.session.commit()
    flash(f"Version {s.version} created as a draft. The approved version {s.version - 1} is kept in the history.", "info")
    return redirect(url_for("samples.edit", pk=pk))


# ----------------------------------------------------------------- bulk import

@bp.route("/bulk", methods=["GET", "POST"])
@role_required("analyst")
def bulk():
    profiles, _ = import_profiles()
    if request.method == "GET":
        return render_template("samples/bulk.html", profiles=profiles, results=None, sample_types=qc.SAMPLE_TYPES,
                               today=date.today().isoformat())
    text = request.form.get("paste", "") or read_upload_text("import_file")
    choice = request.form.get("profile", "auto")
    chosen = [p for p in profiles if p.name == choice]
    parsed = parsing.parse(text, chosen[0]) if chosen else parsing.parse_auto(text, profiles)
    sample_type = request.form.get("sample_type", "Natural")
    analysis_date = parse_date(request.form.get("analysis_date")) or date.today()
    results = []
    for b in parsed.blocks:
        name = b.sample or "(no sample ID)"
        if not b.rows:
            results.append((name, b.element, "skipped: no ratios"))
            continue
        if not b.element or b.element not in library():
            results.append((name, b.element, "skipped: element missing or not in library"))
            continue
        if not b.sample:
            results.append((name, b.element, "skipped: no sample ID in the header line"))
            continue
        if db.session.scalars(select(Sample).where(Sample.element == b.element, Sample.sample_id == b.sample)).first():
            results.append((name, b.element, "skipped: sample ID already exists"))
            continue
        if sample_type == "Enriched":
            results.append((name, b.element, "skipped: enter enriched samples one at a time (enriched isotope needed)"))
            continue
        s = Sample(created_by_id=current_user.id, status="Draft", version=1, sample_id=b.sample, element=b.element,
                   sample_type=sample_type, analysis_date=analysis_date,
                   instrument=request.form.get("instrument", "").strip(), analyst_name=current_user.full_name,
                   n_measurements=int(request.form.get("n_measurements") or 30), rsd_unit=parsed.rsd_unit,
                   entries=[{"label": r.label_raw, "value": r.mean_raw, "rsd": r.rsd_raw} for r in b.rows])
        db.session.add(s)
        outcome = evaluate(s)
        db.session.flush()
        audit("sample_created", "sample", s.id, {"sample_id": s.sample_id, "element": s.element,
                                                  "entries": s.entries, "via": "bulk import"})
        n_err = len(outcome.by_severity("ERROR"))
        n_warn = len(outcome.by_severity("WARNING"))
        results.append((name, b.element, f"created as draft ({n_err} errors, {n_warn} warnings)", s.id))
    db.session.commit()
    return render_template("samples/bulk.html", profiles=profiles, results=results, unread=parsed.unread,
                           sample_types=qc.SAMPLE_TYPES, today=date.today().isoformat())


@bp.app_template_global()
def user_name(user_id):
    u = db.session.get(User, user_id) if user_id else None
    return u.full_name if u else ""


@bp.app_template_global()
def label_norm(label):
    return norm_label(label)
