import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class CalendarBlockType(StrEnum):
    LEAVE = "leave"
    TRAINING = "training"
    MAINTENANCE = "maintenance"
    ROOM_CLOSURE = "room_closure"
    PERSONAL = "personal"
    OTHER = "other"


class CalendarBlock(Base):
    """A one-off blocked period (leave, maintenance, training, room
    closure, ...) for a provider, a room, or both. `provider_user_id` and
    `created_by_user_id` are bare columns with no foreign key, mirroring
    `TenantMembership.user_id`'s established precedent.

    Removal goes through an explicit service method that emits an audit
    event before/with the delete - a block is never silently hard-deleted
    without history, matching task.md's "Predпочети explicit removal with
    audit" guidance; unlike `Appointment`, there is no soft-cancel status
    for a block (blocks are not something a patient shows up for), so a
    genuine row delete is acceptable as long as it is audited."""

    __tablename__ = "calendar_blocks"
    __table_args__ = (
        Index("ix_calendar_blocks_tenant_id", "tenant_id"),
        Index(
            "ix_calendar_blocks_tenant_provider_range", "tenant_id", "provider_user_id", "starts_at"
        ),
        Index("ix_calendar_blocks_tenant_room_range", "tenant_id", "room_id", "starts_at"),
        CheckConstraint("starts_at < ends_at", name="ck_calendar_blocks_start_before_end"),
        CheckConstraint(
            "provider_user_id IS NOT NULL OR room_id IS NOT NULL",
            name="ck_calendar_blocks_provider_or_room",
        ),
        CheckConstraint("length(btrim(reason)) > 0", name="ck_calendar_blocks_reason_not_blank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    provider_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    room_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clinic_rooms.id"), nullable=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    block_type: Mapped[CalendarBlockType] = mapped_column(
        SAEnum(
            CalendarBlockType,
            name="calendar_block_type",
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
