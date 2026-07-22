"""Reusable authorization primitives.

`require_role` is the authoritative check: services call it directly so they
remain independently safe even if a caller bypasses or omits the API-layer
dependency. `require_roles(...)` is the FastAPI dependency factory used at
the API layer purely as an early-rejection convenience on top of the same
check — it is not itself the authorization boundary.
"""

from collections.abc import Callable

from fastapi import Depends

from app.core.errors import ForbiddenError
from app.core.tenant_context import TenantContext, get_tenant_context
from app.models.membership import MembershipRole

READ_ROLES: frozenset[MembershipRole] = frozenset(MembershipRole)
WRITE_ROLES: frozenset[MembershipRole] = frozenset(
    {
        MembershipRole.OWNER,
        MembershipRole.MANAGER,
        MembershipRole.OPERATOR,
        MembershipRole.CONTENT_EDITOR,
    }
)
DELETE_ROLES: frozenset[MembershipRole] = frozenset({MembershipRole.OWNER, MembershipRole.MANAGER})

# --- MED-003: clinic and staff administration -----------------------------
# Only the owner may edit clinic settings (the membership role matrix in
# task.md gives managers view-only access to clinic settings).
CLINIC_WRITE_ROLES: frozenset[MembershipRole] = frozenset({MembershipRole.OWNER})
# Operators and content editors get no staff-roster visibility in this
# slice (task.md scopes operator staff visibility down to "the minimum
# staff information explicitly required by the application", which no
# current feature needs yet - see ARCHITECTURE.md's MED-003 section for the
# documented limitation).
STAFF_READ_ROLES: frozenset[MembershipRole] = frozenset(
    {MembershipRole.OWNER, MembershipRole.MANAGER, MembershipRole.AUDITOR}
)
STAFF_MANAGE_ROLES: frozenset[MembershipRole] = frozenset(
    {MembershipRole.OWNER, MembershipRole.MANAGER}
)


def require_role(context: TenantContext | None, allowed: frozenset[MembershipRole]) -> None:
    """Fails closed: a missing context is rejected the same way a
    disallowed role is - a controlled `ForbiddenError`, never an
    `AttributeError`/500. Never reveals *why* a caller was rejected (no
    role, tenant, or membership details in the message).

    A `TenantContext` with an inactive membership cannot reach this
    function through the normal resolution path: `get_tenant_context` /
    `app.services.tenant_service.resolve_tenant_context` only ever
    construct one from a membership that already passed an active-status
    check, so there is no separate inactive-membership branch to enforce
    here without duplicating that invariant redundantly."""
    if context is None or context.role not in allowed:
        raise ForbiddenError("Not permitted to perform this action.")


def require_roles(*allowed: MembershipRole) -> Callable[[TenantContext], TenantContext]:
    allowed_set = frozenset(allowed)

    def _dependency(context: TenantContext = Depends(get_tenant_context)) -> TenantContext:
        require_role(context, allowed_set)
        return context

    return _dependency
