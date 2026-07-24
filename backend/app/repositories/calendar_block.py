import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.calendar_block import CalendarBlock, CalendarBlockType


class CalendarBlockRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, tenant_id: uuid.UUID, block_id: uuid.UUID) -> CalendarBlock | None:
        stmt = select(CalendarBlock).where(
            CalendarBlock.tenant_id == tenant_id, CalendarBlock.id == block_id
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def list_in_range(
        self,
        tenant_id: uuid.UUID,
        *,
        range_start: datetime,
        range_end: datetime,
        provider_user_id: uuid.UUID | None = None,
        room_id: uuid.UUID | None = None,
    ) -> list[CalendarBlock]:
        """Every block overlapping `[range_start, range_end)` - half-open,
        matching the appointment-interval convention used everywhere
        else in this module."""
        conditions = [
            CalendarBlock.tenant_id == tenant_id,
            CalendarBlock.starts_at < range_end,
            CalendarBlock.ends_at > range_start,
        ]
        if provider_user_id is not None:
            conditions.append(CalendarBlock.provider_user_id == provider_user_id)
        if room_id is not None:
            conditions.append(CalendarBlock.room_id == room_id)
        stmt = select(CalendarBlock).where(*conditions).order_by(CalendarBlock.starts_at.asc())
        return list(self._db.execute(stmt).scalars().all())

    def list_affecting_provider_or_room(
        self,
        tenant_id: uuid.UUID,
        *,
        range_start: datetime,
        range_end: datetime,
        provider_user_id: uuid.UUID,
        room_id: uuid.UUID | None,
    ) -> list[CalendarBlock]:
        """Blocks the availability engine must subtract for one specific
        provider/room pair: any block naming that provider, OR (if a room
        is given) any block naming that room, overlapping the range."""
        room_condition = CalendarBlock.room_id == room_id if room_id is not None else None
        provider_condition = CalendarBlock.provider_user_id == provider_user_id
        target_condition = (
            or_(provider_condition, room_condition)
            if room_condition is not None
            else provider_condition
        )
        stmt = (
            select(CalendarBlock)
            .where(
                CalendarBlock.tenant_id == tenant_id,
                CalendarBlock.starts_at < range_end,
                CalendarBlock.ends_at > range_start,
                target_condition,
            )
            .order_by(CalendarBlock.starts_at.asc())
        )
        return list(self._db.execute(stmt).scalars().all())

    def create(
        self,
        tenant_id: uuid.UUID,
        *,
        provider_user_id: uuid.UUID | None,
        room_id: uuid.UUID | None,
        starts_at: datetime,
        ends_at: datetime,
        reason: str,
        block_type: CalendarBlockType,
        created_by_user_id: uuid.UUID,
    ) -> CalendarBlock:
        block = CalendarBlock(
            tenant_id=tenant_id,
            provider_user_id=provider_user_id,
            room_id=room_id,
            starts_at=starts_at,
            ends_at=ends_at,
            reason=reason,
            block_type=block_type,
            created_by_user_id=created_by_user_id,
        )
        self._db.add(block)
        self._db.flush()
        return block

    def update(
        self,
        tenant_id: uuid.UUID,
        block_id: uuid.UUID,
        *,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        reason: str | None = None,
        block_type: CalendarBlockType | None = None,
    ) -> CalendarBlock | None:
        block = self.get_by_id(tenant_id, block_id)
        if block is None:
            return None
        if starts_at is not None:
            block.starts_at = starts_at
        if ends_at is not None:
            block.ends_at = ends_at
        if reason is not None:
            block.reason = reason
        if block_type is not None:
            block.block_type = block_type
        self._db.flush()
        return block

    def delete(self, tenant_id: uuid.UUID, block_id: uuid.UUID) -> CalendarBlock | None:
        block = self.get_by_id(tenant_id, block_id)
        if block is None:
            return None
        self._db.delete(block)
        self._db.flush()
        return block
