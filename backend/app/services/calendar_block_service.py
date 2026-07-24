import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.audit import AuditEvent, AuditOutcome, emit_audit_event
from app.core.authorization import CALENDAR_CONFIG_ROLES, CALENDAR_READ_ROLES, require_role
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.tenant_context import TenantContext
from app.models.calendar_block import CalendarBlock, CalendarBlockType
from app.repositories.calendar_block import CalendarBlockRepository
from app.repositories.clinic_room import ClinicRoomRepository

_RESOURCE_TYPE = "calendar_block"
MAX_BLOCK_RANGE_DAYS = 31


class CalendarBlockService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = CalendarBlockRepository(db)
        self._rooms = ClinicRoomRepository(db)

    def list_in_range(
        self,
        context: TenantContext,
        *,
        range_start: datetime,
        range_end: datetime,
        provider_user_id: uuid.UUID | None,
        room_id: uuid.UUID | None,
    ) -> list[CalendarBlock]:
        require_role(context, CALENDAR_READ_ROLES)
        if range_end <= range_start:
            raise ConflictError("date_to must be after date_from.")
        if (range_end - range_start).days > MAX_BLOCK_RANGE_DAYS:
            raise ConflictError(f"Date range may not exceed {MAX_BLOCK_RANGE_DAYS} days.")
        return self._repo.list_in_range(
            context.tenant_id,
            range_start=range_start,
            range_end=range_end,
            provider_user_id=provider_user_id,
            room_id=room_id,
        )

    def get(self, context: TenantContext, block_id: uuid.UUID) -> CalendarBlock:
        require_role(context, CALENDAR_READ_ROLES)
        block = self._repo.get_by_id(context.tenant_id, block_id)
        if block is None:
            raise NotFoundError()
        return block

    def create(
        self,
        context: TenantContext,
        *,
        provider_user_id: uuid.UUID | None,
        room_id: uuid.UUID | None,
        starts_at: datetime,
        ends_at: datetime,
        reason: str,
        block_type: CalendarBlockType,
    ) -> CalendarBlock:
        try:
            require_role(context, CALENDAR_CONFIG_ROLES)
            if provider_user_id is None and room_id is None:
                raise ConflictError("A block must reference a provider, a room, or both.")
            if starts_at >= ends_at:
                raise ConflictError("starts_at must be before ends_at.")
            if room_id is not None:
                room = self._rooms.get_by_id(context.tenant_id, room_id)
                if room is None:
                    raise NotFoundError("Room not found in this clinic.")
        except (ForbiddenError, ConflictError, NotFoundError):
            self._audit(context, "calendar.block_created", AuditOutcome.REJECTED)
            raise

        block = self._repo.create(
            context.tenant_id,
            provider_user_id=provider_user_id,
            room_id=room_id,
            starts_at=starts_at,
            ends_at=ends_at,
            reason=reason,
            block_type=block_type,
            created_by_user_id=context.user_id,
        )
        self._db.commit()
        self._audit(context, "calendar.block_created", AuditOutcome.SUCCESS, block.id)
        return block

    def update(
        self,
        context: TenantContext,
        block_id: uuid.UUID,
        *,
        starts_at: datetime | None,
        ends_at: datetime | None,
        reason: str | None,
        block_type: CalendarBlockType | None,
    ) -> CalendarBlock:
        try:
            require_role(context, CALENDAR_CONFIG_ROLES)
        except ForbiddenError:
            self._audit(context, "calendar.block_updated", AuditOutcome.REJECTED, block_id)
            raise

        existing = self._repo.get_by_id(context.tenant_id, block_id)
        if existing is None:
            self._audit(context, "calendar.block_updated", AuditOutcome.REJECTED, block_id)
            raise NotFoundError()

        new_start = starts_at if starts_at is not None else existing.starts_at
        new_end = ends_at if ends_at is not None else existing.ends_at
        if new_start >= new_end:
            self._audit(context, "calendar.block_updated", AuditOutcome.REJECTED, block_id)
            raise ConflictError("starts_at must be before ends_at.")

        block = self._repo.update(
            context.tenant_id,
            block_id,
            starts_at=starts_at,
            ends_at=ends_at,
            reason=reason,
            block_type=block_type,
        )
        assert block is not None
        self._db.commit()
        self._audit(context, "calendar.block_updated", AuditOutcome.SUCCESS, block.id)
        return block

    def remove(self, context: TenantContext, block_id: uuid.UUID) -> None:
        try:
            require_role(context, CALENDAR_CONFIG_ROLES)
        except ForbiddenError:
            self._audit(context, "calendar.block_removed", AuditOutcome.REJECTED, block_id)
            raise

        block = self._repo.delete(context.tenant_id, block_id)
        if block is None:
            self._audit(context, "calendar.block_removed", AuditOutcome.REJECTED, block_id)
            raise NotFoundError()

        self._db.commit()
        self._audit(context, "calendar.block_removed", AuditOutcome.SUCCESS, block_id)

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
