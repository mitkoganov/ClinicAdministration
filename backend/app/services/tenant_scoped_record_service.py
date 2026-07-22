"""Service for the internal tenant-scoped demonstration entity.

Re-checks the role permitted for each action itself (authoritative — see
`app.core.authorization`) rather than trusting only the API-layer
dependency, so this service stays safe to call from any future caller.
Emits an audit event for every success and every rejected attempt
(cross-tenant or insufficient-role).

Owns the commit for every mutation it performs, and commits BEFORE emitting
a `SUCCESS` audit event — never after. If the commit itself fails, the
exception propagates and no success audit event is ever emitted, so an
audit log entry marked `success` is always backed by durably persisted
data, never by a mutation that was later rolled back."""

import uuid

from sqlalchemy.orm import Session

from app.core.audit import AuditEvent, AuditOutcome, emit_audit_event
from app.core.authorization import DELETE_ROLES, READ_ROLES, WRITE_ROLES, require_role
from app.core.errors import ForbiddenError, NotFoundError
from app.core.tenant_context import TenantContext
from app.models.tenant_scoped_record import TenantScopedRecord
from app.repositories.tenant_scoped_record import TenantScopedRecordRepository

_RESOURCE_TYPE = "tenant_scoped_record"


class TenantScopedRecordService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = TenantScopedRecordRepository(db)

    def create(self, context: TenantContext, name: str) -> TenantScopedRecord:
        try:
            require_role(context, WRITE_ROLES)
        except ForbiddenError:
            self._audit(context, "tenant_scoped_record.create", AuditOutcome.REJECTED)
            raise
        record = self._repo.create(context.tenant_id, name)
        self._db.commit()
        self._audit(context, "tenant_scoped_record.create", AuditOutcome.SUCCESS, record.id)
        return record

    def get(self, context: TenantContext, record_id: uuid.UUID) -> TenantScopedRecord:
        require_role(context, READ_ROLES)
        record = self._repo.get(context.tenant_id, record_id)
        if record is None:
            self._audit(context, "tenant_scoped_record.get", AuditOutcome.REJECTED, record_id)
            raise NotFoundError("Tenant-scoped record not found")
        return record

    def list(self, context: TenantContext) -> list[TenantScopedRecord]:
        require_role(context, READ_ROLES)
        return self._repo.list(context.tenant_id)

    def update(self, context: TenantContext, record_id: uuid.UUID, name: str) -> TenantScopedRecord:
        try:
            require_role(context, WRITE_ROLES)
        except ForbiddenError:
            self._audit(context, "tenant_scoped_record.update", AuditOutcome.REJECTED, record_id)
            raise
        record = self._repo.update(context.tenant_id, record_id, name)
        if record is None:
            self._audit(context, "tenant_scoped_record.update", AuditOutcome.REJECTED, record_id)
            raise NotFoundError("Tenant-scoped record not found")
        self._db.commit()
        self._audit(context, "tenant_scoped_record.update", AuditOutcome.SUCCESS, record_id)
        return record

    def delete(self, context: TenantContext, record_id: uuid.UUID) -> None:
        try:
            require_role(context, DELETE_ROLES)
        except ForbiddenError:
            self._audit(context, "tenant_scoped_record.delete", AuditOutcome.REJECTED, record_id)
            raise
        deleted = self._repo.delete(context.tenant_id, record_id)
        if not deleted:
            self._audit(context, "tenant_scoped_record.delete", AuditOutcome.REJECTED, record_id)
            raise NotFoundError("Tenant-scoped record not found")
        self._db.commit()
        self._audit(context, "tenant_scoped_record.delete", AuditOutcome.SUCCESS, record_id)

    def _audit(
        self,
        context: TenantContext,
        event_type: str,
        outcome: AuditOutcome,
        record_id: uuid.UUID | None = None,
    ) -> None:
        emit_audit_event(
            AuditEvent(
                event_type=event_type,
                actor_user_id=context.user_id,
                target_resource_type=_RESOURCE_TYPE,
                outcome=outcome,
                tenant_id=context.tenant_id,
                target_resource_id=record_id,
            )
        )
