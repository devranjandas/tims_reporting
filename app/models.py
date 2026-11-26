"""Database tables (SQLite)."""

from __future__ import annotations

from datetime import date, datetime

from flask_login import UserMixin
from sqlalchemy import JSON, ForeignKey, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, login_manager

ROLES = ("viewer", "analyst", "reviewer", "admin")
ROLE_NAMES = {"viewer": "Viewer", "analyst": "Analyst", "reviewer": "Reviewer", "admin": "Administrator"}
INITIALS_PATTERN = r"[A-Z][A-Z0-9]{1,5}"  # e.g. AL, RKS, AB2
# The three activities recorded on a sample: (field, label on forms, wording in the report)
ACTIVITIES = (("filament_by", "Filament preparation by", "Filamenting by"),
              ("sample_prep_by", "Sample preparation by", "Sample preparation by"),
              ("analysis_by", "Analysis by", "Analysis by"))
STATUSES = ("Draft", "Submitted", "Approved", "Rejected")


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


class User(UserMixin, db.Model):
    """A person known to the system.

    * A TIMS workflow person (``in_workflow``) can be picked for filament preparation,
      sample preparation and analysis.
    * An application user (``has_login``) has a username, password and role and can log in.
    A person can be either, or both. People are never deleted, only made inactive.
    """

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), unique=True, default=None)  # only with a login
    full_name: Mapped[str] = mapped_column(String(120))
    initials: Mapped[str] = mapped_column(String(8), unique=True)  # shown in sample forms and reports
    designation: Mapped[str] = mapped_column(String(120), default="")
    division: Mapped[str] = mapped_column(String(120), default="")
    group_name: Mapped[str] = mapped_column(String(120), default="")  # "group" is a reserved word in SQL
    in_workflow: Mapped[bool] = mapped_column(default=True)
    has_login: Mapped[bool] = mapped_column(default=True)
    role: Mapped[str] = mapped_column(String(16), default="analyst")
    password_hash: Mapped[str | None] = mapped_column(String(256), default=None)
    active: Mapped[bool] = mapped_column(default=True)
    must_change_password: Mapped[bool] = mapped_column(default=True)
    failed_logins: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=now)

    @property
    def is_active(self) -> bool:  # used by Flask-Login: only active people with a login can be logged in
        return self.active and self.has_login

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return bool(self.has_login and self.password_hash) and check_password_hash(self.password_hash, password)

    def signatory(self) -> dict:
        """The details printed in a report's name block."""
        return {"name": self.full_name, "designation": self.designation, "division": self.division,
                "group": self.group_name}

    def can(self, *roles: str) -> bool:
        """Role check. Administrators can do everything; reviewers can do everything analysts can."""
        if self.role == "admin":
            return True
        if self.role == "reviewer" and "analyst" in roles:
            return True
        return self.role in roles

    @property
    def role_name(self) -> str:
        return ROLE_NAMES.get(self.role, self.role)


@login_manager.user_loader
def _load_user(user_id: str):
    u = db.session.get(User, int(user_id))
    return u if u is not None and u.is_active else None  # login removed or person made inactive: log out


class Sample(db.Model):
    __table_args__ = (UniqueConstraint("element", "sample_id", name="uq_sample_element_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sample_id: Mapped[str] = mapped_column(String(80))
    element: Mapped[str] = mapped_column(String(4))
    sample_type: Mapped[str] = mapped_column(String(16))
    analysis_date: Mapped[date | None]
    instrument: Mapped[str] = mapped_column(String(80), default="")
    analyst_name: Mapped[str] = mapped_column(String(120), default="")  # full names of analysis_by, for search
    # People involved, as lists of user ids (each list may hold several people)
    analysis_by: Mapped[list] = mapped_column(JSON, default=list)
    filament_by: Mapped[list] = mapped_column(JSON, default=list)
    sample_prep_by: Mapped[list] = mapped_column(JSON, default=list)
    n_measurements: Mapped[int] = mapped_column(default=30)
    run_id: Mapped[str] = mapped_column(String(80), default="")
    batch_ref: Mapped[str] = mapped_column(String(80), default="")
    enriched_isotope: Mapped[int | None]
    reference_sample: Mapped[str] = mapped_column(String(80), default="")  # chosen batch reference (sample ID)
    remarks: Mapped[str] = mapped_column(Text, default="")
    rsd_unit: Mapped[str] = mapped_column(String(10), default="ppm")
    entries: Mapped[list] = mapped_column(JSON, default=list)  # [{label, value, rsd}] as typed
    results: Mapped[dict | None] = mapped_column(JSON, default=None)  # CalcResult as dict
    findings: Mapped[list] = mapped_column(JSON, default=list)
    reference_info: Mapped[dict | None] = mapped_column(JSON, default=None)  # what the checks compared with
    status: Mapped[str] = mapped_column(String(12), default="Draft")
    version: Mapped[int] = mapped_column(default=1)
    lock_version: Mapped[int] = mapped_column(default=1)  # changes on every save (FR-DATA-06)
    element_def_version: Mapped[str] = mapped_column(String(40), default="")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now)
    submitted_at: Mapped[datetime | None]
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    reviewed_at: Mapped[datetime | None]
    review_comment: Mapped[str] = mapped_column(Text, default="")

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    reviewed_by: Mapped[User | None] = relationship(foreign_keys=[reviewed_by_id])
    versions: Mapped[list["SampleVersion"]] = relationship(back_populates="sample", order_by="SampleVersion.version")

    @property
    def editable(self) -> bool:
        return self.status in ("Draft", "Rejected")


class SampleVersion(db.Model):
    """Frozen copy of a sample each time it is approved (and before any revision)."""

    __table_args__ = (UniqueConstraint("sample_pk", "version", name="uq_sample_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sample_pk: Mapped[int] = mapped_column(ForeignKey("sample.id"))
    version: Mapped[int]
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=now)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    reason: Mapped[str] = mapped_column(Text, default="")

    sample: Mapped[Sample] = relationship(back_populates="versions")


class Baseline(db.Model):
    __table_args__ = (UniqueConstraint("element", "version", name="uq_baseline_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    element: Mapped[str] = mapped_column(String(4))
    version: Mapped[int]
    active: Mapped[bool] = mapped_column(default=True)
    members: Mapped[list] = mapped_column(JSON)  # [{id, sample_id, version}]
    stats: Mapped[dict] = mapped_column(JSON)  # {label: {mean, sd, n}}
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=now)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))

    created_by: Mapped[User] = relationship()


class SavedHeader(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    division_code: Mapped[str] = mapped_column(String(8))
    organisation: Mapped[str] = mapped_column(String(200))
    division: Mapped[str] = mapped_column(String(200))
    section: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    form_number: Mapped[str] = mapped_column(String(80), default="")
    logo: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    logo_mime: Mapped[str] = mapped_column(String(32), default="")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(default=now)

    created_by: Mapped[User] = relationship()

    def fields(self) -> dict:
        return {k: getattr(self, k) for k in ("division_code", "organisation", "division", "section", "address", "form_number")}


class Report(db.Model):
    __table_args__ = (UniqueConstraint("report_id", "revision", name="uq_report_revision"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[str] = mapped_column(String(60))
    revision: Mapped[int] = mapped_column(default=0)
    draft: Mapped[bool] = mapped_column(default=False)
    file_stem: Mapped[str] = mapped_column(String(80), unique=True)
    content: Mapped[str] = mapped_column(String(20))
    layout: Mapped[str] = mapped_column(String(20))
    sample_list: Mapped[str] = mapped_column(Text, default="")  # for searching
    revision_reason: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(default=now)

    created_by: Mapped[User] = relationship()


class ReportCounter(db.Model):
    year: Mapped[int] = mapped_column(primary_key=True)
    last_seq: Mapped[int] = mapped_column(default=0)


class AuditLog(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=now, index=True)
    username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(60))
    object_type: Mapped[str] = mapped_column(String(40), default="")
    object_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    details: Mapped[dict | None] = mapped_column(JSON, default=None)


class Setting(db.Model):
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[object] = mapped_column(JSON)
