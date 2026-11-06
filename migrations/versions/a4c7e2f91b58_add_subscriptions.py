"""add shop subscriptions

Every shop gets a free week from now. Marion Plumbing Services and Rahaher
use the app free forever; check the match with `flask billing list` and fix
it with `flask billing free <shop>` if a name differs.

Revision ID: a4c7e2f91b58
Revises: e7b3c1f9a246
Create Date: 2026-11-06 12:00:00

"""
from datetime import datetime, timedelta

from alembic import op
import sqlalchemy as sa


revision = "a4c7e2f91b58"
down_revision = "e7b3c1f9a246"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("retailers", sa.Column("trial_ends_at", sa.DateTime(), nullable=True))
    op.add_column("retailers", sa.Column("paid_until", sa.DateTime(), nullable=True))
    op.add_column(
        "retailers",
        sa.Column("billing_exempt", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    retailers = sa.table(
        "retailers",
        sa.column("business_name", sa.String),
        sa.column("trial_ends_at", sa.DateTime),
        sa.column("billing_exempt", sa.Boolean),
    )
    op.execute(retailers.update().values(trial_ends_at=datetime.utcnow() + timedelta(days=7)))
    squashed = sa.func.replace(sa.func.lower(retailers.c.business_name), " ", "")
    op.execute(
        retailers.update()
        .where(sa.or_(squashed.like("marionplumbing%"), squashed.like("rahaher%")))
        .values(billing_exempt=True)
    )

    op.create_table(
        "subscription_payments",
        sa.Column("subscription_payment_id", sa.Integer(), nullable=False),
        sa.Column("retailer_id", sa.Integer(), nullable=False),
        sa.Column("plan", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("phone_number", sa.String(length=15), nullable=True),
        sa.Column("callback_token", sa.String(length=64), nullable=True),
        sa.Column("merchant_request_id", sa.String(length=64), nullable=True),
        sa.Column("checkout_request_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("result_code", sa.String(length=10), nullable=True),
        sa.Column("result_desc", sa.String(length=255), nullable=True),
        sa.Column("mpesa_receipt", sa.String(length=40), nullable=True),
        sa.Column("period_start", sa.DateTime(), nullable=True),
        sa.Column("period_end", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["retailer_id"], ["retailers.retailer_id"]),
        sa.PrimaryKeyConstraint("subscription_payment_id"),
        sa.UniqueConstraint("callback_token"),
        sa.UniqueConstraint("checkout_request_id"),
    )
    op.create_index(
        op.f("ix_subscription_payments_retailer_id"),
        "subscription_payments",
        ["retailer_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_subscription_payments_retailer_id"), table_name="subscription_payments")
    op.drop_table("subscription_payments")
    op.drop_column("retailers", "billing_exempt")
    op.drop_column("retailers", "paid_until")
    op.drop_column("retailers", "trial_ends_at")
