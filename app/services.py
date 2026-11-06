"""Application logic shared by the web pages: settings, audit trail, sample checks, snapshots."""

from __future__ import annotations

from datetime import date
from typing import Any

from flask import current_app
from flask_login import current_user
from sqlalchemy import func, select

from tims_core import parsing, qc
from tims_core.elements import Element, ElementLibrary

from . import db
from .models import AuditLog, Baseline, Sample, SampleVersion, Setting, now

# ----------------------------------------------------------------- libraries

_LIBRARY_CACHE: dict[str, ElementLibrary] = {}


def library() -> ElementLibrary:
    folder = current_app.config["ELEMENTS_DIR"]
    if folder not in _LIBRARY_CACHE:
        _LIBRARY_CACHE[folder] = ElementLibrary(folder)
    return _LIBRARY_CACHE[folder]


def reload_library() -> ElementLibrary:
    _LIBRARY_CACHE.pop(current_app.config["ELEMENTS_DIR"], None)
    return library()


def element(symbol: str) -> Element:
    return library().get(symbol)


def import_profiles() -> tuple[list[parsing.ImportProfile], dict[str, str]]:
    return parsing.load_profiles(current_app.config["IMPORT_PROFILES_DIR"])


# ----------------------------------------------------------------- settings

def get_setting(key: str) -> Any:
    row = db.session.get(Setting, key)
    if row is not None:
        return row.value
    return current_app.config["DEFAULT_SETTINGS"][key]


def report_signing_key() -> bytes:
    """Secret key that signs report JSON files. Kept in the database, so backups include it."""
    row = db.session.get(Setting, "report_signing_key")
    if row is None:
        import secrets
        row = Setting(key="report_signing_key", value=secrets.token_hex(32))
        db.session.add(row)
        db.session.commit()
    return bytes.fromhex(row.value)


def set_setting(key: str, value: Any) -> None:
    row = db.session.get(Setting, key)
    if row is None:
        db.session.add(Setting(key=key, value=value))
    else:
        row.value = value


# ----------------------------------------------------------------- audit trail

def audit(action: str, object_type: str = "", object_id: Any = "", details: dict | None = None,
          username: str | None = None) -> None:
    if username is None:
        username = current_user.username if current_user and current_user.is_authenticated else "-"
    db.session.add(AuditLog(username=username, action=action, object_type=object_type,
                            object_id=str(object_id), details=details))


# ----------------------------------------------------------------- baselines

def active_baseline(symbol: str) -> Baseline | None:
    return db.session.scalars(
        select(Baseline).where(Baseline.element == symbol, Baseline.active.is_(True)).order_by(Baseline.version.desc())
    ).first()


def baseline_for_qc(row: Baseline | None) -> qc.Baseline | None:
    if row is None:
        return None
    stats = {lbl: qc.BaselineStat(s["mean"], s["sd"], s["n"]) for lbl, s in row.stats.items()}
    return qc.Baseline(row.version, stats, current_app.config["BASELINE_MIN_RUNS"])


def ratio_values(sample_or_state) -> dict[str, float]:
    results = sample_or_state.results if isinstance(sample_or_state, Sample) else sample_or_state.get("results")
    return {r["label"]: r["value"] for r in (results or {}).get("ratios", [])}


# ----------------------------------------------------------------- enriched reference

def batch_reference(sample: Sample) -> tuple[Sample | None, str]:
    """Earlier approved measurement used as the expected composition of an enriched sample (FR-QC-41)."""
    q = select(Sample).where(Sample.element == sample.element, Sample.status == "Approved", Sample.id != sample.id)
    if sample.reference_sample:
        ref = db.session.scalars(q.where(Sample.sample_id == sample.reference_sample)).first()
        if ref is None:
            return None, f"chosen reference sample '{sample.reference_sample}' is not an approved {sample.element} sample"
        return ref, f"sample {ref.sample_id} (chosen)"
    if not sample.batch_ref:
        return None, "no batch reference given"
    ref = db.session.scalars(
        q.where(Sample.sample_type == "Enriched", func.lower(Sample.batch_ref) == sample.batch_ref.lower())
        .order_by(Sample.analysis_date.desc(), Sample.reviewed_at.desc())
    ).first()
    if ref is None:
        return None, f"no approved measurement of batch {sample.batch_ref} yet"
    return ref, f"sample {ref.sample_id}, batch {ref.batch_ref}"


# ----------------------------------------------------------------- evaluation

def evaluate(sample: Sample) -> qc.CheckOutcome:
    """Run calculation and checks, and store results and findings on the sample.

    Justifications and overrides are kept for findings whose wording has not changed.
    """
    el = element(sample.element)
    rows = [qc.EntryRow(e.get("label", ""), e.get("value", ""), e.get("rsd", "")) for e in sample.entries]
    baseline_row = active_baseline(sample.element) if sample.sample_type in qc.NATURAL_TYPES else None
    expected = None
    ref_info: dict[str, Any] = {"natural": el.abundance_source}
    if sample.sample_type == "Enriched":
        ref_sample, why = batch_reference(sample)
        ref_info["batch"] = why
        if ref_sample is not None:
            expected = qc.ExpectedComposition(ratio_values(ref_sample), why)
            ref_info["batch_sample"] = ref_sample.sample_id
    if baseline_row is not None:
        ref_info["baseline_version"] = baseline_row.version
    outcome = qc.check_sample(
        el, rows, sample.sample_type, rsd_unit=sample.rsd_unit,
        baseline=baseline_for_qc(baseline_row), expected=expected,
        enriched_isotope=sample.enriched_isotope,
        k=float(get_setting("coverage_k")), rho=float(get_setting("ratio_correlation")),
    )
    old = {(f["key"], f["message"]): f for f in (sample.findings or [])}
    new_findings = []
    for f in outcome.findings:
        d = f.to_dict()
        prev = old.get((d["key"], d["message"]))
        for k in ("justification", "justified_by", "override_comment", "overridden_by"):
            d[k] = prev.get(k, "") if prev else ""
        new_findings.append(d)
    sample.findings = new_findings
    sample.results = outcome.calc.to_dict() if outcome.calc else None
    sample.element_def_version = el.definition_version
    sample.reference_info = ref_info
    return outcome


def open_problems(sample: Sample) -> list[str]:
    """What still blocks submission: unfixed/un-overridden ERRORs and unjustified WARNINGs."""
    problems = []
    for f in sample.findings or []:
        if f["severity"] == qc.ERROR and not f.get("override_comment"):
            problems.append(f"ERROR not resolved: {f['message']}")
        elif f["severity"] == qc.WARNING and not f.get("justification"):
            problems.append(f"WARNING needs a justification: {f['message']}")
    if not sample.results:
        problems.append("No results: fix the entry errors first.")
    return problems


def apply_suggestion(sample: Sample, finding_key: str) -> str:
    f = next((f for f in sample.findings or [] if f["key"] == finding_key and f.get("suggestion")), None)
    if f is None:
        raise ValueError("suggestion not found")
    s = f["suggestion"]
    entries = [dict(e) for e in sample.entries]
    idx = {norm_label(e.get("label", "")): i for i, e in enumerate(entries)}
    i = idx.get(s["label"])
    if i is None:
        raise ValueError("the ratio for this suggestion is no longer in the entry")
    before = dict(entries[i])
    if s["kind"] == "replace_value":
        entries[i]["value"] = s["new_value"]
        what = f"{s['label']} mean {before['value']} → {s['new_value']}"
    elif s["kind"] == "replace_rsd":
        entries[i]["rsd"] = s["new_rsd"]
        if sample.rsd_unit != "ppm":
            sample.rsd_unit = "ppm"
        what = f"{s['label']} RSD {before['rsd']} → {s['new_rsd']} ppm"
    elif s["kind"] == "swap_value_rsd":
        entries[i]["value"], entries[i]["rsd"] = s["new_value"], s["new_rsd"]
        what = f"{s['label']} mean and RSD swapped"
    elif s["kind"] == "swap_rows":
        j = idx.get(s["other_label"])
        if j is None:
            raise ValueError("the other ratio is no longer in the entry")
        for fld in ("value", "rsd"):
            entries[i][fld], entries[j][fld] = entries[j][fld], entries[i][fld]
        what = f"values of {s['label']} and {s['other_label']} swapped"
    else:
        raise ValueError("unknown suggestion")
    sample.entries = entries
    return what


def norm_label(label: str) -> str:
    m = qc._LABEL_RE.match(label or "")
    return f"{int(m['num'])}/{int(m['den'])}" if m else (label or "").strip()


# ----------------------------------------------------------------- versions and snapshots

STATE_FIELDS = ("sample_id", "element", "sample_type", "instrument", "analyst_name", "n_measurements",
                "run_id", "batch_ref", "enriched_isotope", "reference_sample", "remarks", "rsd_unit",
                "entries", "results", "findings", "reference_info", "status", "version", "element_def_version",
                "review_comment")


def sample_state(sample: Sample) -> dict:
    state = {k: getattr(sample, k) for k in STATE_FIELDS}
    state["analysis_date"] = sample.analysis_date.isoformat() if sample.analysis_date else None
    state["reviewed_by"] = sample.reviewed_by.full_name if sample.reviewed_by else None
    state["reviewed_at"] = sample.reviewed_at.isoformat() if sample.reviewed_at else None
    return state


def freeze_version(sample: Sample, reason: str) -> None:
    exists = db.session.scalars(
        select(SampleVersion).where(SampleVersion.sample_pk == sample.id, SampleVersion.version == sample.version)
    ).first()
    if exists:
        exists.snapshot, exists.reason = sample_state(sample), reason
    else:
        db.session.add(SampleVersion(sample_pk=sample.id, version=sample.version, snapshot=sample_state(sample),
                                     created_by_id=current_user.id, reason=reason))


def state_for_version(sample: Sample, version: int) -> dict | None:
    if sample.version == version:
        return sample_state(sample)
    v = db.session.scalars(
        select(SampleVersion).where(SampleVersion.sample_pk == sample.id, SampleVersion.version == version)
    ).first()
    return v.snapshot if v else None


def report_snapshot(state: dict) -> dict:
    """The values a report shows for one sample (URD 9.5)."""
    el = library().elements.get(state["element"])
    res = state.get("results") or {}
    return {
        "sample_id": state["sample_id"],
        "element": state["element"],
        "element_name": el.name if el else state["element"],
        "sample_type": state["sample_type"],
        "status": state["status"],
        "record_version": state["version"],
        "analysis_date": state.get("analysis_date"),
        "instrument": state.get("instrument", ""),
        "analyst": state.get("analyst_name", ""),
        "n_measurements": state.get("n_measurements"),
        "batch_ref": state.get("batch_ref", ""),
        "enriched_isotope": state.get("enriched_isotope"),
        "element_definition_version": state.get("element_def_version", ""),
        "abundance_source": el.abundance_source if el else "",
        "reference_isotope": res.get("reference_isotope"),
        "k": res.get("k"),
        "rho": res.get("rho"),
        "assumed_natural": res.get("assumed_natural", []),
        "ratios": [{"label": r["label"], "value": r["value"], "rsd_ppm": r["rsd_ppm"], "sd": r["sd"], "U": r["U"]}
                   for r in res.get("ratios", [])],
        "atom_percent": [{"isotope": a["isotope"], "value": a["atom_pct"], "U": a["U"], "assumed": a["assumed"]}
                         for a in res.get("abundances", [])],
    }


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def touch(sample: Sample) -> None:
    sample.updated_at = now()
    sample.lock_version = (sample.lock_version or 0) + 1
