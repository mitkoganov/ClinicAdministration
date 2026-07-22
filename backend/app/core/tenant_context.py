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

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.identity import try_get_raw_identity
from app.core.session_dependency import get_current_session_optional
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
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    x_dev_user_id: str | None = Header(default=None, alias="X-Dev-User-Id"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> TenantContext:
    """A production session ALWAYS takes priority: if a valid session
    cookie is present, it is used and the development-header identity is
    never even consulted - a caller cannot use dev headers to override an
    already-authenticated production session (see task.md "dev headers
    да не могат да override-нат production session"). The development
    path is only ever reached when there is no session cookie at all (or
    it is invalid/expired), and even then only if
    `DEVELOPMENT_IDENTITY_ENABLED=true`."""
    # Deferred import: app.services.tenant_service imports TenantContext from
    # this module, so importing it at module scope here would be circular.
    from app.services.tenant_service import resolve_tenant_context

    validated = get_current_session_optional(request, db, settings)
    if validated is not None:
        if validated.session.selected_tenant_id is None:
            raise AppError("No clinic selected for this session.", status_code=409)
        return resolve_tenant_context(db, validated.user.id, validated.session.selected_tenant_id)

    identity = try_get_raw_identity(x_dev_user_id, x_tenant_id, settings)
    if identity is not None:
        return resolve_tenant_context(db, identity.user_id, identity.tenant_id)

    raise AppError("Authentication required.", status_code=401)
