"""Tenant resolution and validation. This is the only place tenant/membership
existence and active-state rules are checked — reused by both the
request-level tenant context dependency and (in the future) background jobs,
so the validation logic never gets duplicated or drifts."""

import uuid

from sqlalchemy.orm import Session

from app.core.background_context import BackgroundTenantContext
from app.core.errors import NotFoundError
from app.core.slug import normalize_and_validate_slug, normalize_slug  # noqa: F401
from app.core.tenant_context import TenantContext
from app.models.membership import MembershipStatus, TenantMembership
from app.models.tenant import Tenant, TenantStatus
from app.repositories.membership import MembershipRepository
from app.repositories.tenant import TenantRepository

__all__ = [
    "normalize_and_validate_slug",
    "normalize_slug",
    "resolve_background_execution_context",
    "resolve_membership",
    "resolve_tenant",
    "resolve_tenant_context",
]


def resolve_tenant(db: Session, tenant_id: uuid.UUID) -> Tenant:
    tenant = TenantRepository(db).get_by_id(tenant_id)
    if tenant is None or tenant.status != TenantStatus.ACTIVE:
        # Deliberately the same exception, with the same default message, as
        # resolve_membership below: unknown tenant, inactive tenant, missing
        # membership, and inactive membership must be indistinguishable to
        # the caller (tenant-enumeration prevention - see SECURITY.md).
        raise NotFoundError()
    return tenant


def resolve_membership(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> TenantMembership:
    membership = MembershipRepository(db).get_membership(tenant_id, user_id)
    if membership is None or membership.status != MembershipStatus.ACTIVE:
        raise NotFoundError()
    return membership


def resolve_tenant_context(db: Session, user_id: uuid.UUID, tenant_id: uuid.UUID) -> TenantContext:
    """Validate, against the database, that the tenant exists and is active
    and that the user has an active membership in it. Raises `NotFoundError`
    (404) uniformly for every failure mode — unknown tenant, inactive
    tenant, missing membership, and inactive membership are all
    indistinguishable to the caller."""
    tenant = resolve_tenant(db, tenant_id)
    membership = resolve_membership(db, tenant.id, user_id)
    return TenantContext(
        user_id=user_id,
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        membership_id=membership.id,
        role=membership.role,
        membership_status=membership.status,
    )


def resolve_background_execution_context(
    db: Session, background_context: BackgroundTenantContext
) -> TenantContext:
    """The explicit, intended entry point for background/worker code: takes
    a `BackgroundTenantContext` carried across a queue boundary and
    re-resolves a fresh `TenantContext` from the database at execution
    time, exactly as a live request would via `resolve_tenant_context`.

    Never trusts a role or active-state flag from the serialized payload -
    `BackgroundTenantContext` carries none by design (identifiers only), so
    the role and active-state returned here always come from a fresh
    database read, never from whatever was true when the job was enqueued.
    Fails closed with the same uniform `NotFoundError` as every other
    resolution path for an unknown/inactive tenant or a missing/inactive
    membership."""
    return resolve_tenant_context(
        db, background_context.actor_user_id, background_context.tenant_id
    )
