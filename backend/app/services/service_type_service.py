import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import AuditEvent, AuditOutcome, emit_audit_event
from app.core.authorization import CALENDAR_CONFIG_ROLES, CALENDAR_READ_ROLES, require_role
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.tenant_context import TenantContext
from app.models.appointment_service_type import AppointmentServiceType, ServiceTypeStatus
from app.repositories.appointment_service_type import AppointmentServiceTypeRepository

_RESOURCE_TYPE = "appointment_service_type"
_UNIQUE_CODE_CONSTRAINT = "uq_appointment_service_types_tenant_code"


def _is_duplicate_code_violation(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _UNIQUE_CODE_CONSTRAINT


class ServiceTypeService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = AppointmentServiceTypeRepository(db)

    def list(
        self, context: TenantContext, *, status: ServiceTypeStatus | None, limit: int, offset: int
    ) -> tuple[list[AppointmentServiceType], int]:
        require_role(context, CALENDAR_READ_ROLES)
        return self._repo.list_by_tenant(
            context.tenant_id, status=status, limit=limit, offset=offset
        )

    def get(self, context: TenantContext, service_type_id: uuid.UUID) -> AppointmentServiceType:
        require_role(context, CALENDAR_READ_ROLES)
        service_type = self._repo.get_by_id(context.tenant_id, service_type_id)
        if service_type is None:
            raise NotFoundError()
        return service_type

    def create(
        self,
        context: TenantContext,
        *,
        name: str,
        code: str,
        description: str | None,
        default_duration_minutes: int,
        buffer_before_minutes: int,
        buffer_after_minutes: int,
    ) -> AppointmentServiceType:
        try:
            require_role(context, CALENDAR_CONFIG_ROLES)
        except ForbiddenError:
            self._audit(context, "calendar.service_type_created", AuditOutcome.REJECTED)
            raise

        if self._repo.get_by_code(context.tenant_id, code) is not None:
            self._audit(context, "calendar.service_type_created", AuditOutcome.REJECTED)
            raise ConflictError("A service type with this code already exists in this clinic.")

        try:
            service_type = self._repo.create(
                context.tenant_id,
                name=name,
                code=code,
                description=description,
                default_duration_minutes=default_duration_minutes,
                buffer_before_minutes=buffer_before_minutes,
                buffer_after_minutes=buffer_after_minutes,
            )
        except IntegrityError as exc:
            self._db.rollback()
            if not _is_duplicate_code_violation(exc):
                raise
            self._audit(context, "calendar.service_type_created", AuditOutcome.REJECTED)
            raise ConflictError(
                "A service type with this code already exists in this clinic."
            ) from exc

        self._db.commit()
        self._audit(context, "calendar.service_type_created", AuditOutcome.SUCCESS, service_type.id)
        return service_type

    def update(
        self,
        context: TenantContext,
        service_type_id: uuid.UUID,
        *,
        name: str | None,
        description: str | None,
        default_duration_minutes: int | None,
        buffer_before_minutes: int | None,
        buffer_after_minutes: int | None,
    ) -> AppointmentServiceType:
        try:
            require_role(context, CALENDAR_CONFIG_ROLES)
        except ForbiddenError:
            self._audit(
                context, "calendar.service_type_updated", AuditOutcome.REJECTED, service_type_id
            )
            raise

        service_type = self._repo.update(
            context.tenant_id,
            service_type_id,
            name=name,
            description=description,
            default_duration_minutes=default_duration_minutes,
            buffer_before_minutes=buffer_before_minutes,
            buffer_after_minutes=buffer_after_minutes,
        )
        if service_type is None:
            self._audit(
                context, "calendar.service_type_updated", AuditOutcome.REJECTED, service_type_id
            )
            raise NotFoundError()

        self._db.commit()
        self._audit(context, "calendar.service_type_updated", AuditOutcome.SUCCESS, service_type.id)
        return service_type

    def deactivate(
        self, context: TenantContext, service_type_id: uuid.UUID
    ) -> AppointmentServiceType:
        try:
            require_role(context, CALENDAR_CONFIG_ROLES)
        except ForbiddenError:
            self._audit(
                context, "calendar.service_type_deactivated", AuditOutcome.REJECTED, service_type_id
            )
            raise

        service_type = self._repo.update(
            context.tenant_id, service_type_id, status=ServiceTypeStatus.INACTIVE
        )
        if service_type is None:
            self._audit(
                context, "calendar.service_type_deactivated", AuditOutcome.REJECTED, service_type_id
            )
            raise NotFoundError()

        self._db.commit()
        self._audit(
            context, "calendar.service_type_deactivated", AuditOutcome.SUCCESS, service_type.id
        )
        return service_type

    def _audit(
        self,
        context: TenantContext,
        event_type: str,
        outcome: AuditOutcome,
        resource_id: uuid.UUID | None = None,
    ) -> None:
        emit_audit_event(
            AuditEvent(
                event_type=event_type,
                actor_user_id=context.user_id if context is not None else None,
                target_resource_type=_RESOURCE_TYPE,
                outcome=outcome,
                tenant_id=context.tenant_id if context is not None else None,
                target_resource_id=resource_id,
            )
        )
