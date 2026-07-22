"""add authentication foundation

Revision ID: 65f7891a7fc7
Revises: a3f7c9e21d05
Create Date: 2026-07-22 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "65f7891a7fc7"
down_revision: str | None = "a3f7c9e21d05"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "inactive", name="user_account_status", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column(
            "email_verification_state",
            sa.Enum(
                "unverified",
                "verified",
                name="email_verification_state",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_user_accounts"),
    )
    op.create_index(
        "ix_user_accounts_normalized_email", "user_accounts", ["normalized_email"], unique=True
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("selected_tenant_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_accounts.id"], name="fk_auth_sessions_user_id_user_accounts"
        ),
        sa.ForeignKeyConstraint(
            ["selected_tenant_id"],
            ["tenants.id"],
            name="fk_auth_sessions_selected_tenant_id_tenants",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
    )
    op.create_index(
        "ix_auth_sessions_session_token_hash", "auth_sessions", ["session_token_hash"], unique=True
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"], unique=False)
    op.create_index(
        "ix_auth_sessions_absolute_expires_at",
        "auth_sessions",
        ["absolute_expires_at"],
        unique=False,
    )

    op.create_table(
        "one_time_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "password_reset",
                "invitation_accept",
                name="one_time_token_purpose",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column(
            "invited_role",
            sa.Enum(
                "owner",
                "manager",
                "operator",
                "content_editor",
                "auditor",
                name="one_time_token_invited_role",
                native_enum=False,
                length=30,
            ),
            nullable=True,
        ),
        sa.Column("invitee_email", sa.String(length=320), nullable=True),
        sa.Column("inviter_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_accounts.id"], name="fk_one_time_tokens_user_id_user_accounts"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_one_time_tokens_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["inviter_user_id"],
            ["user_accounts.id"],
            name="fk_one_time_tokens_inviter_user_id_user_accounts",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_one_time_tokens"),
    )
    op.create_index("ix_one_time_tokens_token_hash", "one_time_tokens", ["token_hash"], unique=True)
    op.create_index("ix_one_time_tokens_user_id", "one_time_tokens", ["user_id"], unique=False)
    op.create_index(
        "ix_one_time_tokens_expires_at", "one_time_tokens", ["expires_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_one_time_tokens_expires_at", table_name="one_time_tokens")
    op.drop_index("ix_one_time_tokens_user_id", table_name="one_time_tokens")
    op.drop_index("ix_one_time_tokens_token_hash", table_name="one_time_tokens")
    op.drop_table("one_time_tokens")

    op.drop_index("ix_auth_sessions_absolute_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_session_token_hash", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.drop_index("ix_user_accounts_normalized_email", table_name="user_accounts")
    op.drop_table("user_accounts")
