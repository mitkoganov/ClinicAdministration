"""Request-level tenant context.

`get_tenant_context` is the single reusable FastAPI dependency every
tenant-scoped route must depend on. It never trusts a client-supplied tenant
identifier by itself: it re-resolves the tenant and membership from the
database on every request (see `app.services.tenant_service`). There is no
global mutable tenant variable anywhere — `TenantContext` is a plain
per-request object threaded through `Depends()`.
"""

import uuid
from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.identity import RawIdentity, get_raw_identity
from app.db.session import get_db
from app.models.membership import MembershipRole, MembershipStatus


@dataclass(frozen=True)
class TenantContext:
    """Invariant: `membership_status` is always `ACTIVE` here. Every
    construction path goes through `resolve_tenant_context` /
    `resolve_membership`, which already reject an inactive membership
    before a `TenantContext` is ever built - so an inactive-membership
    `TenantContext` cannot exist through the normal resolution path."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    membership_id: uuid.UUID
    role: MembershipRole
    membership_status: MembershipStatus


def get_tenant_context(
    identity: RawIdentity = Depends(get_raw_identity),
    db: Session = Depends(get_db),
) -> TenantContext:
    # Deferred import: app.services.tenant_service imports TenantContext from
    # this module, so importing it at module scope here would be circular.
    from app.services.tenant_service import resolve_tenant_context

    return resolve_tenant_context(db, identity.user_id, identity.tenant_id)
