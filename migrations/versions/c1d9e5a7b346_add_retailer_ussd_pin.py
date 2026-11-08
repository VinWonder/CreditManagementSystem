"""add a USSD PIN for shop owners

Revision ID: c1d9e5a7b346
Revises: b8d3f5a2c719
Create Date: 2026-11-08 10:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "c1d9e5a7b346"
down_revision = "b8d3f5a2c719"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("retailers") as batch_op:
        batch_op.add_column(sa.Column("ussd_pin_hash", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column("ussd_pin_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("ussd_pin_locked_until", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("retailers") as batch_op:
        batch_op.drop_column("ussd_pin_locked_until")
        batch_op.drop_column("ussd_pin_attempts")
        batch_op.drop_column("ussd_pin_hash")
