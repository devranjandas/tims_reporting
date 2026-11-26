"""people without login, division and group

People can now be TIMS workflow persons without a login (only name and initials
recorded), application users with a login, or both.

Adds to user: division, group_name, in_workflow, has_login. Username and password
become optional (only people with a login have them).
Existing users keep their login and become workflow persons, because they may
already be ticked on samples. Division and group start empty.

Revision ID: 0003_people_accounts
Revises: 0002_people
Create Date: 2026-11-26

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0003_people_accounts'
down_revision = '0002_people'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('division', sa.String(length=120), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('group_name', sa.String(length=120), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('in_workflow', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('has_login', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.alter_column('username', existing_type=sa.String(length=64), nullable=True)
        batch_op.alter_column('password_hash', existing_type=sa.String(length=256), nullable=True)


def downgrade():
    # People without a login cannot exist in the older structure: give them a placeholder
    # username and an unusable password so the columns can be made required again.
    op.execute("UPDATE \"user\" SET username = 'nologin-' || id WHERE username IS NULL")
    op.execute("UPDATE \"user\" SET password_hash = '!' WHERE password_hash IS NULL")
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column('password_hash', existing_type=sa.String(length=256), nullable=False)
        batch_op.alter_column('username', existing_type=sa.String(length=64), nullable=False)
        batch_op.drop_column('has_login')
        batch_op.drop_column('in_workflow')
        batch_op.drop_column('group_name')
        batch_op.drop_column('division')
