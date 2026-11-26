"""Database migrations: new, old (pre-migration) and out-of-date databases."""

import shutil
import sqlite3
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from app import create_app, db
from app import dbmaint

ROOT = Path(__file__).resolve().parents[1]
HEAD = ScriptDirectory(str(ROOT / "migrations")).get_current_head()  # latest migration in the code


def make_app(tmp_path, db_file="tims.db"):
    return create_app({
        "INSTANCE_DIR": str(tmp_path / "instance"),
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / db_file}",
        "ELEMENTS_DIR": str(ROOT / "elements"),
        "TESTING": True,
    })


def revision(db_path) -> str | None:
    con = sqlite3.connect(db_path)
    try:
        return con.execute("select version_num from alembic_version").fetchone()[0]
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def test_models_match_migrations(tmp_path):
    """Fails when app/models.py changed without a new migration (see migrations/README)."""
    app = make_app(tmp_path)
    with app.app_context():
        with db.engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), db.metadata)
    assert diff == [], f"models and migrations differ; add a migration: {diff}"


def test_new_database_is_created_at_latest_version(tmp_path):
    make_app(tmp_path)
    assert revision(tmp_path / "tims.db") == HEAD
    assert not (tmp_path / "instance" / "backups").exists()  # nothing to back up for a new database


def test_database_from_before_migrations_is_adopted(tmp_path):
    # An earlier version of the app made its tables with create_all() and had no version record.
    path = tmp_path / "tims.db"
    con = sqlite3.connect(path)
    con.executescript((ROOT / "tests" / "legacy_schema.sql").read_text())
    con.execute("insert into user (username, full_name, designation, role, password_hash, active, "
                "must_change_password, failed_logins, created_at) values "
                "('old', 'Old User', '', 'analyst', 'x', 1, 0, 0, '2026-11-01 10:00:00')")
    con.execute("insert into report (report_id, revision, draft, file_stem, content, layout, sample_list, "
                "revision_reason, created_by_id, created_at) values ('TIMS/A/2026/0001', 0, 0, 'TIMS_A_2026_0001', "
                "'ratios', 'format_a', '', '', 1, '2026-11-01 10:00:00')")
    insert_linked_sample(con, user_id=1)
    con.commit()
    con.close()
    app = make_app(tmp_path)
    assert revision(path) == HEAD
    with app.app_context():
        from app.models import Report, Sample, User
        old = db.session.query(User).filter_by(username="old").one()
        assert (old.full_name, old.initials) == ("Old User", "OU")  # initials made from the name (0002)
        assert (old.has_login, old.in_workflow, old.division) == (True, True, "")  # existing users keep logins (0003)
        assert db.session.query(Report).one().created_by_id == 1
        s = db.session.query(Sample).one()
        assert (s.analyst_name, s.analysis_by, len(s.versions)) == ("Old User", [], 1)
        with db.engine.connect() as conn:  # now identical to a database made by the migrations
            assert compare_metadata(MigrationContext.configure(conn), db.metadata) == []
    assert len(list((tmp_path / "instance" / "backups").glob("pre-upgrade-None-to-pre-migrations-*.db"))) == 0
    assert len(list((tmp_path / "instance" / "backups").glob("pre-upgrade-pre-migrations-to-0001_initial-*.db"))) == 1


def insert_linked_sample(con, user_id):
    """A sample with an approved-version row pointing at it: rebuilding the sample table must keep the link."""
    con.execute("insert into sample (id, sample_id, element, sample_type, instrument, analyst_name, n_measurements, "
                "run_id, batch_ref, reference_sample, remarks, rsd_unit, entries, findings, status, version, "
                "lock_version, element_def_version, created_by_id, created_at, updated_at, review_comment) values "
                "(1, 'S-1', 'Yb', 'Natural', 'TIMS-1', 'Old User', 30, '', '', '', '', 'ppm', '[]', '[]', 'Approved', "
                f"1, 1, 'v1', {user_id}, '2026-11-01 10:00:00', '2026-11-01 10:00:00', '')")
    con.execute("insert into sample_version (sample_pk, version, snapshot, created_at, created_by_id, reason) values "
                f"(1, 1, '{{}}', '2026-11-01 10:00:00', {user_id}, 'approved')")


NEXT_MIGRATION = '''
from alembic import op
import sqlalchemy as sa

revision = "9999_test"
down_revision = "{head}"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("sample", schema=None, recreate="always") as batch_op:  # force a table rebuild
        batch_op.add_column(sa.Column("test_note", sa.String(length=40), nullable=True))


def downgrade():
    with op.batch_alter_table("sample", schema=None) as batch_op:
        batch_op.drop_column("test_note")
'''


def test_out_of_date_database_is_backed_up_and_upgraded(tmp_path, monkeypatch):
    path = tmp_path / "tims.db"
    app = make_app(tmp_path)
    with app.app_context():
        from app.models import User
        u = User(username="keep", full_name="Keep Me", initials="KM", role="analyst")
        u.set_password("x" * 12)
        db.session.add(u)
        db.session.commit()
        uid = u.id
    con = sqlite3.connect(path)
    insert_linked_sample(con, uid)  # rows that point at the table being rebuilt
    con.commit()
    con.close()
    # A newer release adds a migration
    newer = tmp_path / "migrations"
    shutil.copytree(ROOT / "migrations", newer, ignore=shutil.ignore_patterns("__pycache__"))
    (newer / "versions" / "9999_test.py").write_text(NEXT_MIGRATION.replace("{head}", HEAD))
    monkeypatch.setattr(dbmaint, "MIGRATIONS_DIR", newer)
    make_app(tmp_path)
    assert revision(path) == "9999_test"
    con = sqlite3.connect(path)
    assert "test_note" in [r[1] for r in con.execute("pragma table_info(sample)")]
    assert con.execute("select full_name from user where username='keep'").fetchone()[0] == "Keep Me"
    assert con.execute("select count(*) from sample_version where sample_pk = 1").fetchone()[0] == 1
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    con.close()
    backups = list((tmp_path / "instance" / "backups").glob(f"pre-upgrade-{HEAD}-to-9999_test-*.db"))
    assert len(backups) == 1
    assert revision(backups[0]) == HEAD  # the backup is the database as it was before
