"""add shopkeepers, payment approvals and balance audit

Revision ID: 7f3b92c41d60
Revises: e3c8e03a3750
Create Date: 2026-10-19 07:10:00

"""
from alembic import op
import sqlalchemy as sa


revision = "7f3b92c41d60"
down_revision = "e3c8e03a3750"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shopkeepers",
        sa.Column("shopkeeper_id", sa.Integer(), nullable=False),
        sa.Column("retailer_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=100), nullable=False),
        sa.Column("phone_number", sa.String(length=20), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["retailer_id"], ["retailers.retailer_id"]),
        sa.PrimaryKeyConstraint("shopkeeper_id"),
    )
    op.create_index(
        op.f("ix_shopkeepers_retailer_id"), "shopkeepers", ["retailer_id"], unique=False
    )
    op.create_index(
        op.f("ix_shopkeepers_phone_number"), "shopkeepers", ["phone_number"], unique=True
    )

    with op.batch_alter_table("payments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "approval_status",
                sa.String(length=10),
                server_default="approved",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("submitted_by_shopkeeper_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("reviewed_by_retailer_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("review_note", sa.String(length=255), nullable=True))
        batch_op.create_foreign_key(
            "fk_payments_submitted_by_shopkeeper",
            "shopkeepers",
            ["submitted_by_shopkeeper_id"],
            ["shopkeeper_id"],
        )
        batch_op.create_foreign_key(
            "fk_payments_reviewed_by_retailer",
            "retailers",
            ["reviewed_by_retailer_id"],
            ["retailer_id"],
        )

    op.create_table(
        "balance_adjustments",
        sa.Column("adjustment_id", sa.Integer(), nullable=False),
        sa.Column("retailer_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("amount_change", sa.Numeric(10, 2), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("created_by_retailer_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_retailer_id"], ["retailers.retailer_id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.customer_id"]),
        sa.ForeignKeyConstraint(["retailer_id"], ["retailers.retailer_id"]),
        sa.PrimaryKeyConstraint("adjustment_id"),
    )


def downgrade():
    op.drop_table("balance_adjustments")
    with op.batch_alter_table("payments", schema=None) as batch_op:
        batch_op.drop_constraint("fk_payments_reviewed_by_retailer", type_="foreignkey")
        batch_op.drop_constraint("fk_payments_submitted_by_shopkeeper", type_="foreignkey")
        batch_op.drop_column("review_note")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by_retailer_id")
        batch_op.drop_column("submitted_by_shopkeeper_id")
        batch_op.drop_column("approval_status")
    op.drop_index(op.f("ix_shopkeepers_phone_number"), table_name="shopkeepers")
    op.drop_index(op.f("ix_shopkeepers_retailer_id"), table_name="shopkeepers")
    op.drop_table("shopkeepers")
