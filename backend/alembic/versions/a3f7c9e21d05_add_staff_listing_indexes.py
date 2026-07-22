"""add staff listing indexes

Revision ID: a3f7c9e21d05
Revises: 698717db91c1
Create Date: 2026-07-22 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "a3f7c9e21d05"
down_revision: str | None = "698717db91c1"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # MED-003: clinic staff-roster listing (GET /api/v1/clinic/staff)
    # filters by role and/or status within a tenant, in addition to always
    # scoping by tenant_id - these composite indexes support that query
    # pattern. No new tables: a "clinic" is the existing `tenants` table
    # (see ARCHITECTURE.md's MED-003 section), and staff are the existing
    # `tenant_memberships` rows.
    op.create_index(
        "ix_tenant_memberships_tenant_id_role",
        "tenant_memberships",
        ["tenant_id", "role"],
        unique=False,
    )
    op.create_index(
        "ix_tenant_memberships_tenant_id_status",
        "tenant_memberships",
        ["tenant_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_memberships_tenant_id_status", table_name="tenant_memberships")
    op.drop_index("ix_tenant_memberships_tenant_id_role", table_name="tenant_memberships")
