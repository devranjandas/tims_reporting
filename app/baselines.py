"""Lab baseline: set-up from approved natural samples, versions, control charts (FR-QC-11..14)."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from markupsafe import Markup, escape
from sqlalchemy import select

from tims_core import qc

from . import db
from .models import Baseline, Sample
from .services import active_baseline, audit, element, library, ratio_values
from .views_util import role_required

bp = Blueprint("baselines", __name__, url_prefix="/baselines")


def candidates(symbol: str) -> list[Sample]:
    return db.session.scalars(
        select(Sample).where(Sample.element == symbol, Sample.status == "Approved",
                             Sample.sample_type.in_(qc.NATURAL_TYPES))
        .order_by(Sample.analysis_date, Sample.id)
    ).all()


@bp.route("/")
@role_required("analyst")
def index():
    rows = []
    for symbol in library().symbols():
        b = active_baseline(symbol)
        n = min((s["n"] for s in b.stats.values()), default=0) if b else 0
        rows.append({"symbol": symbol, "name": element(symbol).name, "baseline": b, "n": n,
                     "active": n >= current_app.config["BASELINE_MIN_RUNS"]})
    return render_template("baselines/index.html", rows=rows, min_runs=current_app.config["BASELINE_MIN_RUNS"])


@bp.route("/<symbol>", methods=["GET", "POST"])
@role_required("analyst")
def edit(symbol: str):
    if symbol not in library():
        abort(404)
    el = element(symbol)
    current = active_baseline(symbol)
    pool = candidates(symbol)
    history = db.session.scalars(select(Baseline).where(Baseline.element == symbol)
                                 .order_by(Baseline.version.desc())).all()
    if request.method == "POST":
        if not current_user.can("reviewer"):
            abort(403)
        selected_ids = {int(x) for x in request.form.getlist("member")}
    else:
        selected_ids = {m["id"] for m in current.members} if current else set()
        if request.args.get("add", "").isdigit():
            selected_ids.add(int(request.args["add"]))
    selected = [s for s in pool if s.id in selected_ids]
    runs = [ratio_values(s) for s in selected]
    stats = qc.baseline_stats(runs) if runs else {}
    outliers = qc.baseline_outliers(runs, [s.sample_id for s in selected]) if len(runs) >= 4 else []

    if request.method == "POST" and request.form.get("action") == "save":
        if not selected:
            flash("Select at least one sample.", "error")
        else:
            note = request.form.get("note", "").strip()
            if outliers and not note:
                flash("Some runs look like outliers. Remove them, or add a note explaining why they are kept.", "error")
            else:
                version = (history[0].version + 1) if history else 1
                for b in history:
                    b.active = False
                b = Baseline(element=symbol, version=version, active=True, note=note, created_by_id=current_user.id,
                             members=[{"id": s.id, "sample_id": s.sample_id, "version": s.version} for s in selected],
                             stats={lbl: {"mean": st.mean, "sd": st.sd, "n": st.n} for lbl, st in stats.items()})
                db.session.add(b)
                audit("baseline_saved", "baseline", f"{symbol} v{version}",
                      {"members": [s.sample_id for s in selected], "outliers": outliers, "note": note})
                db.session.commit()
                flash(f"Baseline version {version} for {el.name} saved ({len(selected)} runs).", "info")
                return redirect(url_for("baselines.edit", symbol=symbol))

    return render_template("baselines/edit.html", el=el, current=current, pool=pool, selected_ids=selected_ids,
                           stats=stats, outliers=outliers, history=history,
                           min_runs=current_app.config["BASELINE_MIN_RUNS"],
                           charts=control_charts(el, pool, current))


def control_charts(el, samples: list[Sample], baseline: Baseline | None) -> list[tuple[str, Markup]]:
    """One small SVG control chart per ratio (FR-QC-14)."""
    charts = []
    members = {m["id"] for m in baseline.members} if baseline else set()
    for label in el.expected_labels():
        pts = [(s, ratio_values(s).get(label)) for s in samples]
        pts = [(s, v) for s, v in pts if v is not None]
        st = baseline.stats.get(label) if baseline else None
        if not pts:
            continue
        charts.append((label, _svg_chart(pts, st, members)))
    return charts


def _svg_chart(points, st, members, w=640, h=200, pad=48) -> Markup:
    vals = [v for _, v in points]
    lines = []
    if st and st["sd"] > 0:
        lines = [(st["mean"] + k * st["sd"], k) for k in (-3, -2, 0, 2, 3)]
    lo = min(vals + [y for y, _ in lines])
    hi = max(vals + [y for y, _ in lines])
    if hi == lo:
        hi, lo = hi * 1.001 + 1e-12, lo * 0.999 - 1e-12
    span = hi - lo
    lo, hi = lo - 0.08 * span, hi + 0.08 * span

    def y(v):
        return pad / 2 + (hi - v) / (hi - lo) * (h - pad)

    n = len(points)

    def x(i):
        return pad + (i + 0.5) * (w - pad - 12) / n

    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="control chart">',
             f'<rect x="{pad}" y="{pad / 2}" width="{w - pad - 12}" height="{h - pad}" class="chart-bg"/>']
    for v, k in lines:
        cls = "c-mean" if k == 0 else ("c-2" if abs(k) == 2 else "c-3")
        parts.append(f'<line x1="{pad}" x2="{w - 12}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="{cls}"/>')
        txt = "mean" if k == 0 else f"{k:+d} SD"
        parts.append(f'<text x="{w - 14}" y="{y(v) - 3:.1f}" class="c-lbl" text-anchor="end">{txt}</text>')
    for v in (hi - 0.08 * (hi - lo) / 1.16, lo + 0.08 * (hi - lo) / 1.16):
        parts.append(f'<text x="{pad - 4}" y="{y(v) + 4:.1f}" class="c-axis" text-anchor="end">{v:.6g}</text>')
    for i, (s, v) in enumerate(points):
        cls = "pt-member" if s.id in members else "pt"
        tip = escape(f"{s.sample_id} ({s.analysis_date or ''}): {v:.6g}")
        parts.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="4" class="{cls}"><title>{tip}</title></circle>')
    parts.append("</svg>")
    return Markup("".join(parts))
