"""add M-Pesa STK Push

Revision ID: e4b19c7d2a58
Revises: d7a3f0b6c912
Create Date: 2026-10-21 14:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "e4b19c7d2a58"
down_revision = "d7a3f0b6c912"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "customers",
        sa.Column(
            "accepts_stk_prompts", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.create_table(
        "mpesa_settings",
        sa.Column("mpesa_settings_id", sa.Integer(), nullable=False),
        sa.Column("retailer_id", sa.Integer(), nullable=False),
        sa.Column("environment", sa.String(length=10), nullable=False),
        sa.Column("account_type", sa.String(length=10), nullable=False),
        sa.Column("business_shortcode", sa.String(length=10), nullable=False),
        sa.Column("till_number", sa.String(length=10), nullable=True),
        sa.Column("consumer_key_encrypted", sa.Text(), nullable=False),
        sa.Column("consumer_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("passkey_encrypted", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["retailer_id"], ["retailers.retailer_id"]),
        sa.PrimaryKeyConstraint("mpesa_settings_id"),
        sa.UniqueConstraint("retailer_id"),
    )
    op.create_table(
        "stk_push_requests",
        sa.Column("stk_request_id", sa.Integer(), nullable=False),
        sa.Column("retailer_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("phone_number", sa.String(length=15), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("account_reference", sa.String(length=12), nullable=False),
        sa.Column("callback_token", sa.String(length=64), nullable=False),
        sa.Column("merchant_request_id", sa.String(length=64), nullable=True),
        sa.Column("checkout_request_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("result_code", sa.String(length=10), nullable=True),
        sa.Column("result_desc", sa.String(length=255), nullable=True),
        sa.Column("mpesa_receipt", sa.String(length=20), nullable=True),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.customer_id"]),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.payment_id"]),
        sa.ForeignKeyConstraint(["retailer_id"], ["retailers.retailer_id"]),
        sa.PrimaryKeyConstraint("stk_request_id"),
        sa.UniqueConstraint("callback_token"),
        sa.UniqueConstraint("checkout_request_id"),
    )
    op.create_index(
        "ix_stk_push_requests_customer_id", "stk_push_requests", ["customer_id"]
    )
    op.create_index(
        "ix_stk_push_requests_retailer_id", "stk_push_requests", ["retailer_id"]
    )


def downgrade():
    op.drop_index("ix_stk_push_requests_retailer_id", table_name="stk_push_requests")
    op.drop_index("ix_stk_push_requests_customer_id", table_name="stk_push_requests")
    op.drop_table("stk_push_requests")
    op.drop_table("mpesa_settings")
    op.drop_column("customers", "accepts_stk_prompts")
