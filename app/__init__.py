"""TIMS-Report Flask application."""

from __future__ import annotations

import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, redirect, render_template, request, session, url_for
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import MetaData, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase

import tims_core

ROOT = Path(__file__).resolve().parents[1]


class Base(DeclarativeBase):
    # Fixed names for indexes and constraints, so migrations can change them on SQLite.
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


db = SQLAlchemy(model_class=Base)
login_manager = LoginManager()
csrf = CSRFProtect()
migrate = Migrate()


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    """WAL lets users read while one writes; busy_timeout makes writers wait instead of failing."""
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()


def _secret_key(instance: Path) -> str:
    if os.environ.get("TIMS_SECRET_KEY"):
        return os.environ["TIMS_SECRET_KEY"]
    path = instance / "secret_key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


def create_app(overrides: dict | None = None) -> Flask:
    instance = Path(os.environ.get("TIMS_INSTANCE_DIR", ROOT / "instance"))
    if overrides and overrides.get("INSTANCE_DIR"):
        instance = Path(overrides["INSTANCE_DIR"])
    instance.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__, instance_path=str(instance))
    app.config.update(
        SECRET_KEY=_secret_key(instance),
        SQLALCHEMY_DATABASE_URI=os.environ.get("TIMS_DATABASE_URI", f"sqlite:///{instance / 'tims.db'}"),
        ELEMENTS_DIR=os.environ.get("TIMS_ELEMENTS_DIR", str(ROOT / "elements")),
        IMPORT_PROFILES_DIR=os.environ.get("TIMS_IMPORT_PROFILES_DIR", str(ROOT / "import_profiles")),
        REPORTS_DIR=str(instance / "reports"),
        HISTORY_DIR=str(instance / "history"),
        BACKUP_DIR=os.environ.get("TIMS_BACKUP_DIR", str(instance / "backups")),
        BACKUP_KEEP=int(os.environ.get("TIMS_BACKUP_KEEP", "14")),
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=int(os.environ.get("TIMS_SESSION_MINUTES", "30"))),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("TIMS_SECURE_COOKIES", "0") == "1",
        MAX_CONTENT_LENGTH=4 * 1024 * 1024,
        PASSWORD_MIN_LENGTH=10,
        LOCKOUT_ATTEMPTS=5,
        LOCKOUT_MINUTES=15,
        BASELINE_MIN_RUNS=10,
        SOFTWARE_VERSION=tims_core.__version__,
        DEFAULT_SETTINGS={
            "coverage_k": 3.0,
            "ratio_correlation": 0.0,
            "signature_labels": ["Prepared by", "Checked / Approved by"],
            "footer_statement": "This report is digitally generated and does not need signatures.",
        },
    )
    if overrides:
        app.config.update(overrides)
    for key in ("REPORTS_DIR", "HISTORY_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)
    from .dbmaint import MIGRATIONS_DIR

    migrate.init_app(app, db, directory=str(MIGRATIONS_DIR), render_as_batch=True)  # batch mode: SQLite ALTER TABLE
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in."

    from . import models  # noqa: F401  (register tables)
    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .baselines import bp as baselines_bp
    from .cli import register_cli
    from .main import bp as main_bp
    from .reports import bp as reports_bp
    from .samples import bp as samples_bp
    from .views_util import register_template_helpers

    for bp in (auth_bp, main_bp, samples_bp, baselines_bp, reports_bp, admin_bp):
        app.register_blueprint(bp)
    register_template_helpers(app)
    register_cli(app)

    with app.app_context():
        from .dbmaint import prepare_database
        from .services import ensure_signing_key

        if prepare_database(app):  # create or upgrade the tables (with a backup first)
            ensure_signing_key()
        db.engine.dispose()  # do not hand an open SQLite connection to forked Gunicorn workers

    @app.before_request
    def _session_and_password():
        session.permanent = True  # idle timeout (FR-DATA-07)
        if (
            current_user.is_authenticated
            and current_user.must_change_password
            and request.endpoint not in ("auth.change_password", "auth.logout", "static")
        ):
            return redirect(url_for("auth.change_password"))
        return None

    @app.errorhandler(CSRFError)
    def _csrf_error(e):
        return render_template("error.html", title="Form expired",
                               message="The form expired or was sent twice. Go back, reload the page and try again."), 400

    @app.errorhandler(403)
    def _forbidden(e):
        return render_template("error.html", title="Not allowed",
                               message="Your role does not allow this action."), 403

    @app.errorhandler(404)
    def _not_found(e):
        return render_template("error.html", title="Not found", message="That page or record does not exist."), 404

    from .pdf import PDFUnavailable

    @app.errorhandler(PDFUnavailable)
    def _no_pdf(e):
        db.session.rollback()  # nothing is saved, and no report number is used up
        app.logger.error("PDF output unavailable: %s", e)
        return render_template("error.html", title="PDF output not available", message=str(e)), 503

    @app.errorhandler(413)
    def _too_large(e):
        return render_template("error.html", title="File too large", message="The uploaded file is too large."), 413

    return app
