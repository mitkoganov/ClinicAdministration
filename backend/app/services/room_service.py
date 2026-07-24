import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import AuditEvent, AuditOutcome, emit_audit_event
from app.core.authorization import CALENDAR_CONFIG_ROLES, CALENDAR_READ_ROLES, require_role
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.tenant_context import TenantContext
from app.models.clinic_room import ClinicRoom, ClinicRoomStatus
from app.repositories.clinic_room import ClinicRoomRepository

_RESOURCE_TYPE = "clinic_room"
_UNIQUE_CODE_CONSTRAINT = "uq_clinic_rooms_tenant_code"


def _is_duplicate_code_violation(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _UNIQUE_CODE_CONSTRAINT


class RoomService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = ClinicRoomRepository(db)

    def list(
        self, context: TenantContext, *, status: ClinicRoomStatus | None, limit: int, offset: int
    ) -> tuple[list[ClinicRoom], int]:
        require_role(context, CALENDAR_READ_ROLES)
        return self._repo.list_by_tenant(
            context.tenant_id, status=status, limit=limit, offset=offset
        )

    def get(self, context: TenantContext, room_id: uuid.UUID) -> ClinicRoom:
        require_role(context, CALENDAR_READ_ROLES)
        room = self._repo.get_by_id(context.tenant_id, room_id)
        if room is None:
            raise NotFoundError()
        return room

    def create(
        self, context: TenantContext, *, name: str, code: str, description: str | None
    ) -> ClinicRoom:
        try:
            require_role(context, CALENDAR_CONFIG_ROLES)
        except ForbiddenError:
            self._audit(context, "calendar.room_created", AuditOutcome.REJECTED)
            raise

        if self._repo.get_by_code(context.tenant_id, code) is not None:
            self._audit(context, "calendar.room_created", AuditOutcome.REJECTED)
            raise ConflictError("A room with this code already exists in this clinic.")

        try:
            room = self._repo.create(
                context.tenant_id, name=name, code=code, description=description
            )
        except IntegrityError as exc:
            self._db.rollback()
            if not _is_duplicate_code_violation(exc):
                raise
            self._audit(context, "calendar.room_created", AuditOutcome.REJECTED)
            raise ConflictError("A room with this code already exists in this clinic.") from exc

        self._db.commit()
        self._audit(context, "calendar.room_created", AuditOutcome.SUCCESS, room.id)
        return room

    def update(
        self,
        context: TenantContext,
        room_id: uuid.UUID,
        *,
        name: str | None,
        description: str | None,
    ) -> ClinicRoom:
        try:
            require_role(context, CALENDAR_CONFIG_ROLES)
        except ForbiddenError:
            self._audit(context, "calendar.room_updated", AuditOutcome.REJECTED, room_id)
            raise

        room = self._repo.update(context.tenant_id, room_id, name=name, description=description)
        if room is None:
            self._audit(context, "calendar.room_updated", AuditOutcome.REJECTED, room_id)
            raise NotFoundError()

        self._db.commit()
        self._audit(context, "calendar.room_updated", AuditOutcome.SUCCESS, room.id)
        return room

    def deactivate(self, context: TenantContext, room_id: uuid.UUID) -> ClinicRoom:
        try:
            require_role(context, CALENDAR_CONFIG_ROLES)
        except ForbiddenError:
            self._audit(context, "calendar.room_deactivated", AuditOutcome.REJECTED, room_id)
            raise

        room = self._repo.update(context.tenant_id, room_id, status=ClinicRoomStatus.INACTIVE)
        if room is None:
            self._audit(context, "calendar.room_deactivated", AuditOutcome.REJECTED, room_id)
            raise NotFoundError()

        self._db.commit()
        self._audit(context, "calendar.room_deactivated", AuditOutcome.SUCCESS, room.id)
        return room

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
