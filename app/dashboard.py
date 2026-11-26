"""Dashboard: samples per year and month, and work done by each person."""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import date

from flask import Blueprint, Response, render_template, request
from flask_login import login_required
from markupsafe import Markup, escape
from sqlalchemy import select

from tims_core import qc

from . import db
from .models import ACTIVITIES, Sample, User
from .services import library, parse_date

bp = Blueprint("dashboard", __name__)

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DONE = ("Approved",)                               # counted by default
DONE_OR_IN_PROGRESS = ("Approved", "Submitted", "Draft")  # with "include unapproved"; rejected never counts


def read_filters() -> dict:
    a = request.args
    return {
        "from": parse_date(a.get("from")), "to": parse_date(a.get("to")),
        "element": a.get("element", ""), "type": a.get("type", ""),
        "all": a.get("all") == "1",
        "year": int(a["year"]) if a.get("year", "").isdigit() else None,
    }


def base_query(f: dict):
    q = select(Sample)
    if f["from"]:
        q = q.where(Sample.analysis_date >= f["from"])
    if f["to"]:
        q = q.where(Sample.analysis_date <= f["to"])
    if f["element"]:
        q = q.where(Sample.element == f["element"])
    if f["type"]:
        q = q.where(Sample.sample_type == f["type"])
    return q


def compute(f: dict) -> dict:
    statuses = DONE_OR_IN_PROGRESS if f["all"] else DONE
    samples = db.session.scalars(base_query(f).where(Sample.status.in_(statuses))).all()
    elements = sorted({s.element for s in samples}, key=lambda e: library().elements[e].atomic_number
                      if e in library() else 999)

    by_year: dict[int, Counter] = defaultdict(Counter)
    no_date = Counter()
    for s in samples:
        if s.analysis_date:
            by_year[s.analysis_date.year][s.element] += 1
        else:
            no_date[s.element] += 1
    years = sorted(by_year)
    year = f["year"] if f["year"] in by_year else (years[-1] if years else date.today().year)
    by_month = [Counter() for _ in range(12)]
    for s in samples:
        if s.analysis_date and s.analysis_date.year == year:
            by_month[s.analysis_date.month - 1][s.element] += 1

    # Work by person. A sample with two analysts counts once for each of them.
    work: dict[int, Counter] = defaultdict(Counter)
    missing = Counter()
    for s in samples:
        for field, _, _ in ACTIVITIES:
            ids = getattr(s, field) or []
            if not ids:
                missing[field] += 1
            for uid in set(ids):
                work[uid][field] += 1
    # Reviews: approvals and rejections, whatever the status filter (a rejection is also review work)
    reviewed = db.session.scalars(base_query(f).where(Sample.status.in_(("Approved", "Rejected")),
                                                      Sample.reviewed_by_id.is_not(None))).all()
    for s in reviewed:
        work[s.reviewed_by_id]["reviewed"] += 1
    people = {u.id: u for u in db.session.scalars(select(User).where(User.id.in_(list(work)))).all()}
    person_rows = sorted(
        ({"person": people[uid], **{k: c.get(k, 0) for k in ("filament_by", "sample_prep_by", "analysis_by", "reviewed")}}
         for uid, c in work.items() if uid in people),
        key=lambda r: (-r["analysis_by"], -r["sample_prep_by"], -r["filament_by"], r["person"].initials))

    return {
        "statuses": statuses, "samples_total": len(samples), "elements": elements,
        "years": [(y, by_year[y]) for y in years], "no_date": no_date,
        "year": year, "all_years": years, "months": list(zip(MONTHS, by_month)),
        "year_total": sum(by_year[year].values()) if year in by_year else 0,
        "person_rows": person_rows, "missing": missing,
    }


@bp.route("/dashboard")
@login_required
def index():
    f = read_filters()
    d = compute(f)
    year_chart = bar_chart([(str(y), sum(c.values())) for y, c in d["years"]], "samples per year")
    month_chart = bar_chart([(m, sum(c.values())) for m, c in d["months"]], f"samples per month in {d['year']}")
    return render_template("dashboard.html", d=d, f=f, args=request.args, year_chart=year_chart,
                           month_chart=month_chart, activities=ACTIVITIES, sample_types=qc.SAMPLE_TYPES,
                           element_symbols=library().symbols())


@bp.app_template_filter("combine_year")
def _combine_year(args: dict, year: int) -> dict:
    return {**args, "year": year}


@bp.route("/dashboard/export.csv")
@login_required
def export_csv():
    f = read_filters()
    d = compute(f)
    table = request.args.get("table", "years")
    out = io.StringIO()
    w = csv.writer(out)
    if table == "people":
        w.writerow(["initials", "name", "filament_preparation", "sample_preparation", "analysis", "reviewed"])
        for r in d["person_rows"]:
            w.writerow([r["person"].initials, r["person"].full_name, r["filament_by"], r["sample_prep_by"],
                        r["analysis_by"], r["reviewed"]])
        w.writerow(["", "not recorded", d["missing"]["filament_by"], d["missing"]["sample_prep_by"],
                    d["missing"]["analysis_by"], ""])
    else:
        rows = d["months"] if table == "months" else d["years"]
        w.writerow(["month" if table == "months" else "year"] + d["elements"] + ["total"])
        for label, c in rows:
            label = f"{label} {d['year']}" if table == "months" else label
            w.writerow([label] + [c.get(e, 0) for e in d["elements"]] + [sum(c.values())])
    name = f"tims_dashboard_{table}.csv"
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={name}"})


def bar_chart(points: list[tuple[str, int]], what: str, w: int = 640, h: int = 220) -> Markup:
    """One-series bar chart as inline SVG: thin bars with rounded tops, faint grid, a tooltip per bar,
    and a value label on the tallest bar only (the table below the chart has every number)."""
    if not points:
        return Markup('<p class="muted">No samples for these filters.</p>')
    left, right, top, bottom = 40, 10, 18, 26
    pw, ph = w - left - right, h - top - bottom
    peak = max(v for _, v in points)
    step = nice_step(peak)
    ymax = max(step, -(-peak // step) * step)
    n = len(points)
    slot = pw / n
    bw = min(36.0, slot * 0.6)
    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart bars" role="img" aria-label="{escape(what)}">']
    for t in range(0, ymax + 1, step):
        y = top + ph - t / ymax * ph
        parts.append(f'<line x1="{left}" x2="{w - right}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 3.5:.1f}" class="c-axis" text-anchor="end">{t}</text>')
    labelled = False
    for i, (label, v) in enumerate(points):
        cx = left + slot * (i + 0.5)
        x0 = cx - bw / 2
        base = top + ph
        bh = v / ymax * ph
        tip = escape(f"{label}: {v} sample{'s' if v != 1 else ''}")
        parts.append(f'<g class="bar"><title>{tip}</title>'
                     f'<rect x="{left + slot * i:.1f}" y="{top}" width="{slot:.1f}" height="{ph}" class="hit"/>')
        if v:
            r = min(4.0, bw / 2, bh)
            parts.append(f'<path d="M{x0:.1f},{base:.1f} V{base - bh + r:.1f} Q{x0:.1f},{base - bh:.1f} {x0 + r:.1f},{base - bh:.1f} '
                         f'H{x0 + bw - r:.1f} Q{x0 + bw:.1f},{base - bh:.1f} {x0 + bw:.1f},{base - bh + r:.1f} V{base:.1f} Z" class="fill"/>')
        parts.append("</g>")
        if v == peak and v and not labelled:
            parts.append(f'<text x="{cx:.1f}" y="{base - bh - 5:.1f}" class="c-val" text-anchor="middle">{v}</text>')
            labelled = True
        parts.append(f'<text x="{cx:.1f}" y="{h - 8}" class="c-axis" text-anchor="middle">{escape(label)}</text>')
    parts.append(f'<line x1="{left}" x2="{w - right}" y1="{top + ph}" y2="{top + ph}" class="baseline"/></svg>')
    return Markup("".join(parts))


def nice_step(peak: int) -> int:
    """Gridline spacing giving about four whole-number lines."""
    for s in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if peak / s <= 4:
            return s
    return 10 ** len(str(peak // 4))
