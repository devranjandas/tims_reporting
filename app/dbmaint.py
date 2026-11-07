"""Database upgrades (migrations) and SQLite backups.

At start-up :func:`prepare_database` brings the database to the structure the code
expects:

* new, empty database: all migrations are applied;
* database made before migrations existed (it has tables but no version record):
  it is recorded as being at the first migration, then upgraded;
* older database: a backup copy is written to ``<instance>/backups`` first, then the
  missing migrations are applied.

Developers who change ``app/models.py`` must add a migration:
``flask --app wsgi db migrate -m "what changed"``, check the generated file in
``migrations/versions/``, and commit it. A test fails if they forget.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from flask import Flask
from flask_migrate import stamp, upgrade
from sqlalchemy import inspect, text

from . import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def backup_sqlite(src: Path, dest: Path) -> Path:
    """Consistent copy of a SQLite database, safe while others are using it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(src)
    target = sqlite3.connect(dest)
    try:
        with target:
            source.backup(target)
    finally:
        source.close()
        target.close()
    return dest


def migration_state(app: Flask) -> tuple[str | None, str | None, str | None, bool]:
    """(current revision, head revision, base revision, database has tables)."""
    script = ScriptDirectory.from_config(app.extensions["migrate"].migrate.get_config())
    with db.engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
        tables = [t for t in inspect(conn).get_table_names() if t != "alembic_version"]
    return current, script.get_current_head(), script.get_base(), bool(tables)


# Tables whose single-column UNIQUE rules had no name before migrations existed.
LEGACY_UNNAMED_UNIQUE = ("user", "report", "saved_header")


def adopt_legacy_database(app: Flask) -> None:
    """Make a database created before migrations existed identical to migration 0001.

    Those databases have three unnamed UNIQUE rules. Each affected table is rebuilt
    from the model definition (same columns, same rows), which gives the rules their
    names. Foreign-key checks are paused while tables are swapped, then verified.
    """
    with db.engine.connect() as conn:
        conn.connection.dbapi_connection.execute("PRAGMA foreign_keys=OFF")  # must run outside a transaction
        try:
            with conn.begin():
                ops = Operations(MigrationContext.configure(conn))
                for name in LEGACY_UNNAMED_UNIQUE:
                    with ops.batch_alter_table(name, recreate="always", copy_from=db.metadata.tables[name]):
                        pass
                problems = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
                if problems:
                    raise RuntimeError(f"foreign key check failed after rebuilding tables: {problems}")
        finally:
            conn.connection.dbapi_connection.execute("PRAGMA foreign_keys=ON")


def prepare_database(app: Flask) -> bool:
    """Bring the database up to date. Returns False only while the first migration is being created."""
    if not (MIGRATIONS_DIR / "env.py").exists():
        return False
    current, head, base, has_tables = migration_state(app)
    if head is None:
        return False  # migrations folder exists but holds no migration yet
    if current == head:
        return True
    if current is None and has_tables:
        # Made by an earlier version of the app with create_all(). Back it up, bring it to
        # exactly the structure of the first migration, and record that.
        backup_before(app, "pre-migrations", base)
        app.logger.warning("Database has no migration record; adopting it as revision %s.", base)
        adopt_legacy_database(app)
        stamp(directory=str(MIGRATIONS_DIR), revision=base)
        current = base
        if current == head:
            return True
    if has_tables:
        backup_before(app, current, head)
    upgrade(directory=str(MIGRATIONS_DIR))
    return True


def backup_before(app: Flask, current: str | None, target: str) -> None:
    db_path = Path(db.engine.url.database)
    stamp_txt = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = Path(app.instance_path) / "backups" / f"pre-upgrade-{current}-to-{target}-{stamp_txt}.db"
    backup_sqlite(db_path, dest)
    app.logger.warning("Database backed up to %s before upgrading %s -> %s.", dest, current, target)
