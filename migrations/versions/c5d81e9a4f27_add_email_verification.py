"""add email verification

Revision ID: c5d81e9a4f27
Revises: b2f4c9d17e35
Create Date: 2026-10-21 10:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "c5d81e9a4f27"
down_revision = "b2f4c9d17e35"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "retailers",
        sa.Column("email_verified_at", sa.DateTime(), nullable=True),
    )
    # Accounts that existed before verification was introduced keep working.
    op.execute(
        "UPDATE retailers SET email_verified_at = COALESCE(created_at, CURRENT_TIMESTAMP) "
        "WHERE email_verified_at IS NULL"
    )
    op.create_table(
        "email_verification_codes",
        sa.Column("verification_id", sa.Integer(), nullable=False),
        sa.Column("retailer_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["retailer_id"], ["retailers.retailer_id"]),
        sa.PrimaryKeyConstraint("verification_id"),
    )
    op.create_index(
        "ix_email_verification_codes_retailer_id",
        "email_verification_codes",
        ["retailer_id"],
    )


def downgrade():
    op.drop_index(
        "ix_email_verification_codes_retailer_id",
        table_name="email_verification_codes",
    )
    op.drop_table("email_verification_codes")
    op.drop_column("retailers", "email_verified_at")
