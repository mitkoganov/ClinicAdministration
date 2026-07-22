import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.membership import MembershipRole, MembershipStatus, TenantMembership
from app.models.tenant import Tenant, TenantStatus


class MembershipRepository:
    """Every method takes `tenant_id` explicitly and issues a single query
    with `tenant_id` in the WHERE clause — there is no method that looks a
    membership up by `id` alone, so a foreign-tenant row and a missing row
    always produce the identical "no row" result."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_membership(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> TenantMembership | None:
        stmt = select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def get_by_id(self, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> TenantMembership | None:
        stmt = select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.id == membership_id,
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        role: MembershipRole | None = None,
        status: MembershipStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[TenantMembership], int]:
        conditions = [TenantMembership.tenant_id == tenant_id]
        if role is not None:
            conditions.append(TenantMembership.role == role)
        if status is not None:
            conditions.append(TenantMembership.status == status)

        total = self._db.execute(
            select(func.count()).select_from(TenantMembership).where(*conditions)
        ).scalar_one()

        # Deterministic order: creation time, then id as a stable tiebreaker
        # for memberships created in the same instant.
        stmt = (
            select(TenantMembership)
            .where(*conditions)
            .order_by(TenantMembership.created_at.asc(), TenantMembership.id.asc())
            .limit(limit)
            .offset(offset)
        )
        items = list(self._db.execute(stmt).scalars().all())
        return items, total

    def create(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, role: MembershipRole
    ) -> TenantMembership:
        membership = TenantMembership(
            tenant_id=tenant_id, user_id=user_id, role=role, status=MembershipStatus.ACTIVE
        )
        self._db.add(membership)
        self._db.flush()
        return membership

    def update(
        self,
        tenant_id: uuid.UUID,
        membership_id: uuid.UUID,
        *,
        role: MembershipRole | None = None,
        status: MembershipStatus | None = None,
    ) -> TenantMembership | None:
        membership = self.get_by_id(tenant_id, membership_id)
        if membership is None:
            return None
        if role is not None:
            membership.role = role
        if status is not None:
            membership.status = status
        self._db.flush()
        return membership

    def list_active_for_user(self, user_id: uuid.UUID) -> list[tuple[Tenant, TenantMembership]]:
        """Every ACTIVE membership this user has in an ACTIVE tenant,
        across all tenants - used only for the user's own "which clinics
        can I access" list (see app.api.auth's GET /clinics), never for
        anything cross-tenant-authorizing. An inactive tenant or inactive
        membership is silently excluded, not merely flagged: this is what
        "clinic selection can only ever land on an active membership"
        means at the query level."""
        stmt = (
            select(Tenant, TenantMembership)
            .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
            .where(
                TenantMembership.user_id == user_id,
                TenantMembership.status == MembershipStatus.ACTIVE,
                Tenant.status == TenantStatus.ACTIVE,
            )
            .order_by(Tenant.name.asc())
        )
        return [(row[0], row[1]) for row in self._db.execute(stmt).all()]

    def lock_active_owner_ids(self, tenant_id: uuid.UUID) -> list[uuid.UUID]:
        """Row-locks (`SELECT ... FOR UPDATE`) every currently active OWNER
        membership in this tenant. Call this, inside the caller's existing
        transaction, before deciding whether a demote/deactivate/remove of an
        owner would leave the clinic without one — a concurrent transaction
        attempting the same kind of change blocks on these rows until this
        one commits or rolls back, closing the obvious last-owner race
        window that a plain SELECT-then-UPDATE would leave open."""
        stmt = (
            select(TenantMembership.id)
            .where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.role == MembershipRole.OWNER,
                TenantMembership.status == MembershipStatus.ACTIVE,
            )
            .with_for_update()
        )
        return list(self._db.execute(stmt).scalars().all())
