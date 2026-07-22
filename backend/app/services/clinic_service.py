"""Service for clinic (tenant) settings administration.

A "clinic" is user-facing terminology for the existing `Tenant` model - see
ARCHITECTURE.md's MED-003 section. Re-checks the role permitted for each
action itself (authoritative - see `app.core.authorization`) rather than
trusting only the API-layer dependency. Owns the commit for its mutation and
commits BEFORE emitting a `SUCCESS` audit event - never after."""

import uuid

from sqlalchemy.orm import Session

from app.core.audit import AuditEvent, AuditOutcome, emit_audit_event
from app.core.authorization import CLINIC_WRITE_ROLES, READ_ROLES, require_role
from app.core.errors import ForbiddenError, NotFoundError
from app.core.tenant_context import TenantContext
from app.models.tenant import Tenant
from app.repositories.tenant import TenantRepository

_RESOURCE_TYPE = "clinic"


class ClinicService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = TenantRepository(db)

    def get(self, context: TenantContext) -> Tenant:
        require_role(context, READ_ROLES)
        tenant = self._repo.get_by_id(context.tenant_id)
        if tenant is None:
            # Structurally unreachable in practice: `context` only exists
            # because `resolve_tenant_context` already loaded this exact
            # tenant as ACTIVE. Kept as a defensive fail-closed branch
            # rather than an assertion, consistent with every other
            # repository-returns-None path in this codebase.
            raise NotFoundError()
        return tenant

    def update(self, context: TenantContext, name: str) -> Tenant:
        try:
            require_role(context, CLINIC_WRITE_ROLES)
        except ForbiddenError:
            self._audit(context, "clinic.update", AuditOutcome.REJECTED)
            raise

        tenant = self._repo.update_name(context.tenant_id, name)
        if tenant is None:
            self._audit(context, "clinic.update", AuditOutcome.REJECTED)
            raise NotFoundError()

        self._db.commit()
        self._audit(context, "clinic.update", AuditOutcome.SUCCESS, tenant.id)
        return tenant

    def _audit(
        self,
        context: TenantContext,
        event_type: str,
        outcome: AuditOutcome,
        target_id: uuid.UUID | None = None,
    ) -> None:
        emit_audit_event(
            AuditEvent(
                event_type=event_type,
                actor_user_id=context.user_id,
                target_resource_type=_RESOURCE_TYPE,
                outcome=outcome,
                tenant_id=context.tenant_id,
                target_resource_id=target_id,
            )
        )
