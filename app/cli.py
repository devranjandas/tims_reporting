"""Command-line tools: create the first administrator, back up the data."""

from __future__ import annotations

import getpass
import shutil
import sqlite3
import tarfile
from datetime import datetime
from pathlib import Path

import click
from flask import current_app
from sqlalchemy import select

from . import db
from .models import User
from .services import audit


def register_cli(app) -> None:
    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.option("--full-name", prompt=True)
    @click.option("--designation", default="", prompt=True)
    def create_admin(username, full_name, designation):
        """Create an administrator account (use once, at installation)."""
        if db.session.scalars(select(User).where(User.username == username)).first():
            raise click.ClickException("That username already exists.")
        password = getpass.getpass("Password: ")
        if len(password) < current_app.config["PASSWORD_MIN_LENGTH"]:
            raise click.ClickException(f"Use at least {current_app.config['PASSWORD_MIN_LENGTH']} characters.")
        if password != getpass.getpass("Repeat password: "):
            raise click.ClickException("The passwords do not match.")
        u = User(username=username, full_name=full_name, designation=designation, role="admin",
                 must_change_password=False)
        u.set_password(password)
        db.session.add(u)
        audit("user_created", "user", username, {"role": "admin", "via": "command line"}, username="system")
        db.session.commit()
        click.echo(f"Administrator {username} created.")

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
            src = sqlite3.connect(db_path)
            out = sqlite3.connect(work / "tims.db")
            with out:
                src.backup(out)  # SQLite online backup: consistent even while users are working
            src.close()
            out.close()
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
