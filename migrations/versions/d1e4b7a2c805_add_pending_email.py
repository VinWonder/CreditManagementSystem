"""add pending email for email changes

Revision ID: d1e4b7a2c805
Revises: c8f2a5d9e317
Create Date: 2026-10-22 22:30:00

"""
from alembic import op
import sqlalchemy as sa


revision = "d1e4b7a2c805"
down_revision = "c8f2a5d9e317"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("retailers", sa.Column("pending_email", sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column("retailers", "pending_email")
