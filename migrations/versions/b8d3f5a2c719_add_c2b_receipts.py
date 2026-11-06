"""add c2b receipts for automatic Paybill confirmation

Revision ID: b8d3f5a2c719
Revises: a4c7e2f91b58
Create Date: 2026-11-06 15:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "b8d3f5a2c719"
down_revision = "a4c7e2f91b58"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "c2b_receipts",
        sa.Column("c2b_receipt_id", sa.Integer(), nullable=False),
        sa.Column("trans_id", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("bill_ref", sa.String(length=40), nullable=True),
        sa.Column("payer_name", sa.String(length=100), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("subscription_payment_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["subscription_payment_id"], ["subscription_payments.subscription_payment_id"]
        ),
        sa.PrimaryKeyConstraint("c2b_receipt_id"),
        sa.UniqueConstraint("trans_id"),
    )


def downgrade():
    op.drop_table("c2b_receipts")
