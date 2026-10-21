"""add per-shop M-Pesa accounts

Revision ID: a9d4e2f7c615
Revises: f2c6a8e1b734
Create Date: 2026-10-21 18:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "a9d4e2f7c615"
down_revision = "f2c6a8e1b734"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mpesa_accounts",
        sa.Column("mpesa_account_id", sa.Integer(), nullable=False),
        sa.Column("retailer_id", sa.Integer(), nullable=False),
        sa.Column("environment", sa.String(length=10), nullable=False),
        sa.Column("account_type", sa.String(length=10), nullable=False),
        sa.Column("shortcode", sa.String(length=10), nullable=False),
        sa.Column("till_number", sa.String(length=10), nullable=True),
        sa.Column("consumer_key_encrypted", sa.Text(), nullable=False),
        sa.Column("consumer_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("passkey_encrypted", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["retailer_id"], ["retailers.retailer_id"]),
        sa.PrimaryKeyConstraint("mpesa_account_id"),
        sa.UniqueConstraint("retailer_id"),
    )


def downgrade():
    op.drop_table("mpesa_accounts")
