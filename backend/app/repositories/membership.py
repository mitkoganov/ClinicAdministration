import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.membership import TenantMembership


class MembershipRepository:
    """Read access to tenant memberships, always scoped to a specific
    (tenant_id, user_id) pair — there is no `list_all` or unscoped lookup."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_membership(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> TenantMembership | None:
        stmt = select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
        )
        return self._db.execute(stmt).scalar_one_or_none()
