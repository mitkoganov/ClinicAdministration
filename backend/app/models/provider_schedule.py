import uuid
from datetime import date, datetime, time
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Time,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class ProviderScheduleStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ProviderSchedule(Base):
    """A recurring WEEKLY availability rule for a provider, expressed in
    the tenant's local wall-clock time (see app.core.timezone) - never in
    UTC. `provider_user_id` is a bare column with no foreign key,
    deliberately mirroring `TenantMembership.user_id`'s precedent: a
    "provider" is simply an existing tenant member (any role) who has at
    least one row here - there is no dedicated `Practitioner` model in
    this codebase (see tasks/current/task.md "Current architecture
    assumptions"). `day_of_week` follows Python's `datetime.weekday()`
    convention (0 = Monday ... 6 = Sunday), NOT the ISO weekday
    convention - document this at every call site that converts a
    calendar date to a day_of_week value.

    Overlapping active rules for the same
    (tenant_id, provider_user_id, day_of_week) with intersecting
    effective-date ranges and intersecting local time ranges are rejected
    at the service layer (see app.services.schedule_service) - not by a
    database constraint. A DB-level guarantee here would need to combine a
    plain `time` range with a nullable `date` range in one exclusion
    constraint, which is materially more awkward than the
    tstzrange-based Appointment constraint below and was judged not worth
    the complexity for this foundation slice; the appointment-vs-
    appointment overlap (the actual double-booking risk) keeps its
    mandatory DB-level protection regardless."""

    __tablename__ = "provider_schedules"
    __table_args__ = (
        Index("ix_provider_schedules_tenant_id", "tenant_id"),
        Index(
            "ix_provider_schedules_tenant_provider_day",
            "tenant_id",
            "provider_user_id",
            "day_of_week",
        ),
        CheckConstraint(
            "day_of_week >= 0 AND day_of_week <= 6", name="ck_provider_schedules_day_of_week_range"
        ),
        CheckConstraint("start_time < end_time", name="ck_provider_schedules_start_before_end"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until >= effective_from",
            name="ck_provider_schedules_effective_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    provider_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    day_of_week: Mapped[int] = mapped_column(nullable=False)
    start_time: Mapped[time] = mapped_column(Time(), nullable=False)
    end_time: Mapped[time] = mapped_column(Time(), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date(), nullable=False)
    effective_until: Mapped[date | None] = mapped_column(Date(), nullable=True)
    room_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clinic_rooms.id"), nullable=True
    )
    status: Mapped[ProviderScheduleStatus] = mapped_column(
        SAEnum(
            ProviderScheduleStatus,
            name="provider_schedule_status",
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ProviderScheduleStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ScheduleBreak(Base):
    """A recurring break (e.g. lunch) inside one `ProviderSchedule` rule's
    local-time window. Deliberately has NO `tenant_id` of its own - every
    query reaches a break through its parent `schedule_id`, which is
    itself tenant-scoped, so duplicating `tenant_id` here would be a
    redundant column with no independent enforcement value; this is a
    documented deviation from the "every table has tenant_id" convention,
    not an oversight."""

    __tablename__ = "schedule_breaks"
    __table_args__ = (
        Index("ix_schedule_breaks_schedule_id", "schedule_id"),
        CheckConstraint("start_time < end_time", name="ck_schedule_breaks_start_before_end"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("provider_schedules.id", ondelete="CASCADE"), nullable=False
    )
    start_time: Mapped[time] = mapped_column(Time(), nullable=False)
    end_time: Mapped[time] = mapped_column(Time(), nullable=False)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
