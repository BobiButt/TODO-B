"""Add authentication fields to users table.

Revision ID: 0002_add_auth_fields
Revises: 0001_initial_schema
Create Date: 2026-10-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_add_auth_fields"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("provider", sa.String(length=10), nullable=False, server_default="email"))
    op.add_column("users", sa.Column("google_id", sa.String(length=255), nullable=True, unique=True))
    op.add_column("users", sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("otp_code", sa.String(length=10), nullable=True))
    op.add_column("users", sa.Column("otp_expires_at", sa.DateTime(timezone=True), nullable=True))

    op.create_check_constraint(
        "ck_users_provider",
        "users",
        "provider IN ('email', 'google')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_provider", "users", type_="check")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "provider")
    op.drop_column("users", "google_id")
    op.drop_column("users", "is_verified")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "otp_code")
    op.drop_column("users", "otp_expires_at")
