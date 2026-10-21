"""add payment method

Revision ID: d7a3f0b6c912
Revises: c5d81e9a4f27
Create Date: 2026-10-21 12:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "d7a3f0b6c912"
down_revision = "c5d81e9a4f27"
branch_labels = None
depends_on = None


def upgrade():
    # Existing payments keep NULL: their method was never recorded.
    op.add_column("payments", sa.Column("payment_method", sa.String(length=20), nullable=True))
    op.add_column("payments", sa.Column("payment_reference", sa.String(length=40), nullable=True))


def downgrade():
    op.drop_column("payments", "payment_reference")
    op.drop_column("payments", "payment_method")
