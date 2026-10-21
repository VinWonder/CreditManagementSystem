"""unique shop names and email inboxes

Revision ID: b3e7f1c9d402
Revises: a9d4e2f7c615
Create Date: 2026-10-21 20:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "b3e7f1c9d402"
down_revision = "a9d4e2f7c615"
branch_labels = None
depends_on = None


def _canonical_email(email):
    # Same rule as app.auth.identity.canonical_email, frozen here for the backfill.
    email = (email or "").strip().lower()
    local, _, domain = email.rpartition("@")
    if not local or not domain:
        return email
    local = local.split("+", 1)[0]
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def upgrade():
    op.add_column("retailers", sa.Column("email_canonical", sa.String(length=255), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT retailer_id, email FROM retailers WHERE email IS NOT NULL")
    ).all()
    for retailer_id, email in rows:
        connection.execute(
            sa.text("UPDATE retailers SET email_canonical = :canonical WHERE retailer_id = :id"),
            {"canonical": _canonical_email(email), "id": retailer_id},
        )
    op.create_unique_constraint("uq_retailers_email_canonical", "retailers", ["email_canonical"])
    op.create_index(
        "uq_retailers_business_name_lower",
        "retailers",
        [sa.text("lower(business_name)")],
        unique=True,
    )


def downgrade():
    op.drop_index("uq_retailers_business_name_lower", table_name="retailers")
    op.drop_constraint("uq_retailers_email_canonical", "retailers", type_="unique")
    op.drop_column("retailers", "email_canonical")
