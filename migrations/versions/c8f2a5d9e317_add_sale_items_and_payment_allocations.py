"""add sale items and payment allocations

Revision ID: c8f2a5d9e317
Revises: b3e7f1c9d402
Create Date: 2026-10-22 09:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "c8f2a5d9e317"
down_revision = "b3e7f1c9d402"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sale_items",
        sa.Column("sale_item_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=150), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["transaction_id"], ["credit_transactions.transaction_id"]),
        sa.PrimaryKeyConstraint("sale_item_id"),
    )
    op.create_index("ix_sale_items_transaction_id", "sale_items", ["transaction_id"])
    op.create_table(
        "payment_allocations",
        sa.Column("allocation_id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("sale_item_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.payment_id"]),
        sa.ForeignKeyConstraint(["sale_item_id"], ["sale_items.sale_item_id"]),
        sa.PrimaryKeyConstraint("allocation_id"),
    )
    op.create_index("ix_payment_allocations_payment_id", "payment_allocations", ["payment_id"])
    op.create_index("ix_payment_allocations_sale_item_id", "payment_allocations", ["sale_item_id"])
    op.add_column("stk_push_requests", sa.Column("sale_item_ids", sa.String(length=500), nullable=True))

    # Every existing sale becomes a one-item sale with the same description and amount.
    op.execute(
        "INSERT INTO sale_items (transaction_id, position, description, amount) "
        "SELECT transaction_id, 0, item_description, amount FROM credit_transactions"
    )


def downgrade():
    op.drop_column("stk_push_requests", "sale_item_ids")
    op.drop_index("ix_payment_allocations_sale_item_id", table_name="payment_allocations")
    op.drop_index("ix_payment_allocations_payment_id", table_name="payment_allocations")
    op.drop_table("payment_allocations")
    op.drop_index("ix_sale_items_transaction_id", table_name="sale_items")
    op.drop_table("sale_items")
