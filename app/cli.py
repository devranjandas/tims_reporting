"""Command-line tools: create the first administrator, back up the data."""

from __future__ import annotations

import getpass
import re
import shutil
import tarfile
from datetime import datetime
from pathlib import Path

import click
from flask import current_app
from sqlalchemy import select

from . import db
from .dbmaint import backup_sqlite
from .models import INITIALS_PATTERN, User, now
from .services import audit


def register_cli(app) -> None:
    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.option("--full-name", prompt=True)
    @click.option("--initials", prompt=True)
    @click.option("--designation", default="", prompt=True)
    def create_admin(username, full_name, initials, designation):
        """Create an administrator account (use once, at installation)."""
        if db.session.scalars(select(User).where(User.username == username)).first():
            raise click.ClickException("That username already exists.")
        initials = initials.strip().upper()
        if not re.fullmatch(INITIALS_PATTERN, initials):
            raise click.ClickException("Initials must be 2–6 capital letters or digits, starting with a letter.")
        if db.session.scalars(select(User).where(User.initials == initials)).first():
            raise click.ClickException("Those initials are already used.")
        password = getpass.getpass("Password: ")
        if len(password) < current_app.config["PASSWORD_MIN_LENGTH"]:
            raise click.ClickException(f"Use at least {current_app.config['PASSWORD_MIN_LENGTH']} characters.")
        if password != getpass.getpass("Repeat password: "):
            raise click.ClickException("The passwords do not match.")
        # An administrator logs in; tick "TIMS workflow person" on the People page if they also do TIMS work.
        u = User(username=username, full_name=full_name, initials=initials, designation=designation, role="admin",
                 has_login=True, in_workflow=False,
                 must_change_password=False)
        u.set_password(password)
        db.session.add(u)
        audit("user_created", "user", username, {"role": "admin", "via": "command line"}, username="system")
        db.session.commit()
        click.echo(f"Administrator {username} created.")

    @app.cli.command("reset-password")
    @click.argument("username")
    def reset_password(username):
        """Set a new password for a login and unlock it (e.g. a forgotten administrator password)."""
        u = db.session.scalars(select(User).where(User.username == username)).first()
        if u is None:
            names = ", ".join(db.session.scalars(select(User.username).where(User.username.is_not(None))).all())
            raise click.ClickException(f"No login named '{username}'. Logins in this database: {names or 'none'}.")
        password = getpass.getpass("New password: ")
        if len(password) < current_app.config["PASSWORD_MIN_LENGTH"]:
            raise click.ClickException(f"Use at least {current_app.config['PASSWORD_MIN_LENGTH']} characters.")
        if password != getpass.getpass("Repeat new password: "):
            raise click.ClickException("The passwords do not match.")
        u.set_password(password)
        u.has_login, u.active = True, True
        u.failed_logins, u.locked_until = 0, None
        u.must_change_password = False
        audit("password_reset", "user", username, {"via": "command line"}, username="system")
        db.session.commit()
        click.echo(f"Password for {username} reset; the login is active and unlocked.")

    @app.cli.command("list-logins")
    def list_logins():
        """Show which logins exist in the database this instance uses."""
        click.echo(f"Database: {db.engine.url.database}")
        for u in db.session.scalars(select(User).where(User.username.is_not(None)).order_by(User.username)).all():
            state = "active" if u.active and u.has_login else "no login" if not u.has_login else "inactive"
            locked = ", locked" if u.locked_until and u.locked_until > now() else ""
            click.echo(f"  {u.username:20} {u.role:9} {state}{locked}  ({u.full_name})")

    @app.cli.command("backup")
    @click.option("--dest", default=None, help="Backup folder (default: TIMS_BACKUP_DIR or instance/backups).")
    def backup(dest):
        """Copy the database (safely, while the app runs) and all report files into one .tar.gz."""
        dest = Path(dest or current_app.config["BACKUP_DIR"])
        dest.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        db_path = Path(db.engine.url.database)
        work = dest / f"tims-{stamp}"
        work.mkdir()
        try:
            backup_sqlite(db_path, work / "tims.db")  # consistent even while users are working
            for name, folder in (("reports", current_app.config["REPORTS_DIR"]),
                                 ("history", current_app.config["HISTORY_DIR"]),
                                 ("elements", current_app.config["ELEMENTS_DIR"]),
                                 ("import_profiles", current_app.config["IMPORT_PROFILES_DIR"])):
                if Path(folder).is_dir():
                    shutil.copytree(folder, work / name)
            archive = dest / f"tims-{stamp}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(work, arcname=work.name)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        keep = current_app.config["BACKUP_KEEP"]
        old = sorted(dest.glob("tims-*.tar.gz"))[:-keep]
        for p in old:
            p.unlink()
        click.echo(f"Backup written to {archive} ({len(old)} old backup(s) removed).")
