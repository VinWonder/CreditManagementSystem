"""STK Push as a payment method

Daraja settings move to the environment, and prompts no longer need a
per-customer consent flag.

Revision ID: f2c6a8e1b734
Revises: e4b19c7d2a58
Create Date: 2026-10-21 16:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "f2c6a8e1b734"
down_revision = "e4b19c7d2a58"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "stk_push_requests",
        sa.Column("requested_by_shopkeeper_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_stk_push_requests_requested_by_shopkeeper_id",
        "stk_push_requests",
        "shopkeepers",
        ["requested_by_shopkeeper_id"],
        ["shopkeeper_id"],
    )
    op.drop_table("mpesa_settings")
    op.drop_column("customers", "accepts_stk_prompts")


def downgrade():
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
    op.drop_constraint(
        "fk_stk_push_requests_requested_by_shopkeeper_id",
        "stk_push_requests",
        type_="foreignkey",
    )
    op.drop_column("stk_push_requests", "requested_by_shopkeeper_id")
