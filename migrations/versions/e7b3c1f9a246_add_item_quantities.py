"""add item quantities and unit prices

Only adds columns: existing items become quantity 1 with their amount as
the unit price, so every total and balance stays the same.

Revision ID: e7b3c1f9a246
Revises: d1e4b7a2c805
Create Date: 2026-10-23 12:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "e7b3c1f9a246"
down_revision = "d1e4b7a2c805"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sale_items", sa.Column("quantity", sa.Integer(), server_default="1", nullable=False))
    op.add_column("sale_items", sa.Column("unit_price", sa.Numeric(precision=10, scale=2), nullable=True))
    op.execute("UPDATE sale_items SET unit_price = amount WHERE unit_price IS NULL")
    op.add_column("payment_allocations", sa.Column("quantity", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("payment_allocations", "quantity")
    op.drop_column("sale_items", "unit_price")
    op.drop_column("sale_items", "quantity")
