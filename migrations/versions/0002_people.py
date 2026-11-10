"""user initials and people on samples

Adds:
* user.initials (required, unique). Existing users get initials made from their
  full name (e.g. "Ana Lyst" -> "AL"), with a number added if two would clash.
  Administrators can change them on the Users page.
* sample.analysis_by, sample.filament_by, sample.sample_prep_by: lists of user ids.
  Existing samples start with empty lists; their free-text analyst name is kept.

Revision ID: 0002_people
Revises: 0001_initial
Create Date: 2026-11-10

"""
import re

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0002_people'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def initials_from_name(full_name: str, username: str, taken: set) -> str:
    words = re.findall(r"[A-Za-z]+", full_name or "") or re.findall(r"[A-Za-z]+", username or "") or ["X"]
    base = "".join(w[0] for w in words).upper()[:5]
    if len(base) < 2:
        base = (words[0][:2].upper() + "X")[:2]
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base[:5]}{n}"
        n += 1
    taken.add(candidate)
    return candidate


def upgrade():
    with op.batch_alter_table('sample', schema=None) as batch_op:
        for col in ('analysis_by', 'filament_by', 'sample_prep_by'):
            batch_op.add_column(sa.Column(col, sa.JSON(), nullable=False, server_default=sa.text("'[]'")))

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('initials', sa.String(length=8), nullable=True))

    conn = op.get_bind()
    taken: set = set()
    for uid, full_name, username in conn.execute(sa.text('SELECT id, full_name, username FROM "user" ORDER BY id')).fetchall():
        conn.execute(sa.text('UPDATE "user" SET initials = :i WHERE id = :id'),
                     {"i": initials_from_name(full_name, username, taken), "id": uid})

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column('initials', existing_type=sa.String(length=8), nullable=False)
        batch_op.create_unique_constraint(batch_op.f('uq_user_initials'), ['initials'])


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('uq_user_initials'), type_='unique')
        batch_op.drop_column('initials')

    with op.batch_alter_table('sample', schema=None) as batch_op:
        for col in ('sample_prep_by', 'filament_by', 'analysis_by'):
            batch_op.drop_column(col)
