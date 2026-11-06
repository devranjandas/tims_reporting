"""Home page."""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select

from . import db
from .models import Report, Sample

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def index():
    if current_user.role == "viewer":
        return redirect(url_for("reports.index"))
    counts = dict(db.session.execute(select(Sample.status, func.count()).group_by(Sample.status)).all())
    to_review = db.session.scalars(
        select(Sample).where(Sample.status == "Submitted").order_by(Sample.submitted_at)
    ).all()
    mine = db.session.scalars(
        select(Sample).where(Sample.created_by_id == current_user.id, Sample.status.in_(["Draft", "Rejected"]))
        .order_by(Sample.updated_at.desc()).limit(20)
    ).all()
    reports = db.session.scalars(select(Report).order_by(Report.created_at.desc()).limit(10)).all()
    return render_template("index.html", counts=counts, to_review=to_review, mine=mine, reports=reports)
