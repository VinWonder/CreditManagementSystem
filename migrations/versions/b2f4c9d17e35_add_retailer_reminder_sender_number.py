"""add retailer reminder sender number

Revision ID: b2f4c9d17e35
Revises: 8a6e2b4c1d73
Create Date: 2026-10-20 16:10:00

"""
from alembic import op
import sqlalchemy as sa


revision = "b2f4c9d17e35"
down_revision = "8a6e2b4c1d73"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "messaging_settings",
        sa.Column("reminder_sender_number", sa.String(length=30), nullable=True),
    )


def downgrade():
    op.drop_column("messaging_settings", "reminder_sender_number")
