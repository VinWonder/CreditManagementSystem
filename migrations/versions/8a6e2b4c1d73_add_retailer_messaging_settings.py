"""add retailer messaging settings

Revision ID: 8a6e2b4c1d73
Revises: 3c61a8f2d954
Create Date: 2026-10-20 13:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "8a6e2b4c1d73"
down_revision = "3c61a8f2d954"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "messaging_settings",
        sa.Column("messaging_settings_id", sa.Integer(), nullable=False),
        sa.Column("retailer_id", sa.Integer(), nullable=False),
        sa.Column("sms_provider", sa.String(length=20), nullable=False),
        sa.Column("at_username", sa.String(length=100), nullable=True),
        sa.Column("at_api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("twilio_account_sid", sa.String(length=100), nullable=True),
        sa.Column("twilio_auth_token_encrypted", sa.Text(), nullable=True),
        sa.Column("twilio_from_number", sa.String(length=30), nullable=True),
        sa.Column("twilio_messaging_service_sid", sa.String(length=100), nullable=True),
        sa.Column("whatsapp_phone_id", sa.String(length=100), nullable=True),
        sa.Column("whatsapp_token_encrypted", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["retailer_id"], ["retailers.retailer_id"]),
        sa.PrimaryKeyConstraint("messaging_settings_id"),
        sa.UniqueConstraint("retailer_id"),
    )


def downgrade():
    op.drop_table("messaging_settings")
