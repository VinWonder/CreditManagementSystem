"""add customer shop and dispatch errors

Revision ID: 3c61a8f2d954
Revises: 7f3b92c41d60
Create Date: 2026-10-19 10:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "3c61a8f2d954"
down_revision = "7f3b92c41d60"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("customers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("shop_name", sa.String(length=100), nullable=True))
    with op.batch_alter_table("message_dispatch_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("failure_reason", sa.String(length=500), nullable=True))


def downgrade():
    with op.batch_alter_table("message_dispatch_log", schema=None) as batch_op:
        batch_op.drop_column("failure_reason")
    with op.batch_alter_table("customers", schema=None) as batch_op:
        batch_op.drop_column("shop_name")
