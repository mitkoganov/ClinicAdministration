import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.authorization import require_calendar_read_or_self
from app.core.errors import ConflictError, NotFoundError
from app.core.scheduling_time import Interval, combine_local
from app.core.scheduling_time import subtract as subtract_intervals
from app.core.tenant_context import TenantContext
from app.core.timezone import resolve_timezone
from app.models.appointment_service_type import ServiceTypeStatus
from app.models.clinic_room import ClinicRoomStatus
from app.models.membership import MembershipStatus
from app.repositories.appointment import AppointmentRepository
from app.repositories.appointment_service_type import AppointmentServiceTypeRepository
from app.repositories.calendar_block import CalendarBlockRepository
from app.repositories.clinic_room import ClinicRoomRepository
from app.repositories.membership import MembershipRepository
from app.repositories.provider_schedule import ProviderScheduleRepository
from app.repositories.tenant import TenantRepository
from app.services.availability_engine import compute_bookable_slots, compute_free_intervals

MAX_AVAILABILITY_RANGE_DAYS = 31


@dataclass(frozen=True)
class AvailableSlot:
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class AvailabilityResult:
    tenant_timezone: str
    provider_user_id: uuid.UUID
    service_type_id: uuid.UUID
    room_id: uuid.UUID | None
    slots: list[AvailableSlot]


class AvailabilityService:
    """The dynamic slot engine - never materializes a slot as a database
    row (see app.services.availability_engine, which holds the actual
    pure computation this class wraps with real repository reads)."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._tenants = TenantRepository(db)
        self._memberships = MembershipRepository(db)
        self._service_types = AppointmentServiceTypeRepository(db)
        self._rooms = ClinicRoomRepository(db)
        self._schedules = ProviderScheduleRepository(db)
        self._blocks = CalendarBlockRepository(db)
        self._appointments = AppointmentRepository(db)

    def get_availability(
        self,
        context: TenantContext,
        *,
        provider_user_id: uuid.UUID,
        service_type_id: uuid.UUID,
        date_from: date,
        date_to: date,
        room_id: uuid.UUID | None,
        now: datetime | None = None,
        granularity_minutes: int | None = None,
    ) -> AvailabilityResult:
        # Task.md authorization matrix: any active member may always view
        # a calendar (including availability) filtered to themselves as
        # the provider, regardless of role - viewing another provider's
        # availability still requires CALENDAR_READ_ROLES.
        require_calendar_read_or_self(context, provider_user_id)

        if date_to < date_from:
            raise ConflictError("date_to must not be before date_from.")
        if (date_to - date_from).days + 1 > MAX_AVAILABILITY_RANGE_DAYS:
            raise ConflictError(f"Date range may not exceed {MAX_AVAILABILITY_RANGE_DAYS} days.")

        tenant = self._tenants.get_by_id(context.tenant_id)
        if tenant is None:
            raise NotFoundError()
        tz = resolve_timezone(tenant.timezone)

        membership = self._memberships.get_membership(context.tenant_id, provider_user_id)
        if membership is None or membership.status != MembershipStatus.ACTIVE:
            raise NotFoundError("Provider not found or not active in this clinic.")

        service_type = self._service_types.get_by_id(context.tenant_id, service_type_id)
        if service_type is None:
            raise NotFoundError("Service type not found in this clinic.")
        if service_type.status != ServiceTypeStatus.ACTIVE:
            raise ConflictError("Service type is not active.")

        if room_id is not None:
            room = self._rooms.get_by_id(context.tenant_id, room_id)
            if room is None:
                raise NotFoundError("Room not found in this clinic.")
            if room.status != ClinicRoomStatus.ACTIVE:
                raise ConflictError("Room is not active.")

        slot_duration = timedelta(minutes=service_type.default_duration_minutes)
        buffer_before = timedelta(minutes=service_type.buffer_before_minutes)
        buffer_after = timedelta(minutes=service_type.buffer_after_minutes)
        granularity = timedelta(
            minutes=granularity_minutes or service_type.default_duration_minutes
        )
        not_before = now or datetime.now(UTC)

        free_intervals = self._free_intervals_for_range(
            context.tenant_id,
            tz,
            provider_user_id=provider_user_id,
            room_id=room_id,
            date_from=date_from,
            date_to=date_to,
            buffer_before=buffer_before,
            buffer_after=buffer_after,
        )
        raw_slots = compute_bookable_slots(free_intervals, slot_duration, granularity, not_before)

        slots = [AvailableSlot(starts_at=s, ends_at=e) for s, e in raw_slots]
        return AvailabilityResult(
            tenant_timezone=tenant.timezone,
            provider_user_id=provider_user_id,
            service_type_id=service_type_id,
            room_id=room_id,
            slots=slots,
        )

    def is_interval_free(
        self,
        tenant_id: uuid.UUID,
        tz_name: str,
        *,
        provider_user_id: uuid.UUID,
        room_id: uuid.UUID | None,
        starts_at: datetime,
        ends_at: datetime,
        buffer_before: timedelta,
        buffer_after: timedelta,
        exclude_appointment_id: uuid.UUID | None = None,
    ) -> bool:
        """Used by AppointmentService to validate a SPECIFIC requested
        interval (create/reschedule) against the same schedule/break/
        block/appointment data `get_availability` uses - never a separate,
        divergent check. Only considers the schedule window(s) covering
        the LOCAL CALENDAR DATE `starts_at` falls on in `tz_name`; an
        appointment is not expected to span midnight in this foundation
        slice."""
        tz = resolve_timezone(tz_name)
        local_date = starts_at.astimezone(tz).date()
        operational_start = starts_at - buffer_before
        operational_end = ends_at + buffer_after

        free_intervals = self._free_intervals_for_range(
            tenant_id,
            tz,
            provider_user_id=provider_user_id,
            room_id=room_id,
            date_from=local_date,
            date_to=local_date,
            buffer_before=buffer_before,
            buffer_after=buffer_after,
            exclude_appointment_id=exclude_appointment_id,
        )
        for free_start, free_end in free_intervals:
            if free_start <= operational_start and operational_end <= free_end:
                return True
        return False

    def diagnose_unavailable_reason(
        self,
        tenant_id: uuid.UUID,
        tz_name: str,
        *,
        provider_user_id: uuid.UUID,
        room_id: uuid.UUID | None,
        starts_at: datetime,
        ends_at: datetime,
        buffer_before: timedelta,
        buffer_after: timedelta,
        exclude_appointment_id: uuid.UUID | None = None,
    ) -> str:
        """Only called after `is_interval_free` has already returned
        `False`, to classify WHY, matching task.md's required
        machine-readable `409` codes. Checked in this specific priority
        order (most specific/actionable first):

        1. an existing blocking `Appointment` overlaps (provider or room)
           -> "appointment_conflict" - the same code the DB exclusion
           constraint itself would raise for a genuine race, so a caller
           sees one consistent code whether the conflict was caught here
           or by the database;
        2. the room is inactive, or a room-scoped `CalendarBlock` overlaps
           -> "room_unavailable";
        3. a provider-scoped `CalendarBlock` overlaps -> "blocked_period";
        4. no `ProviderSchedule` rule matches this day at all ->
           "outside_schedule";
        5. a rule exists for this day but the requested window falls
           outside its hours, inside a break, or against a different
           room -> "provider_unavailable"."""
        operational_start = starts_at - buffer_before
        operational_end = ends_at + buffer_after

        provider_appointment_conflicts = self._appointments.list_blocking_in_range(
            tenant_id,
            range_start=operational_start,
            range_end=operational_end,
            provider_user_id=provider_user_id,
            exclude_appointment_id=exclude_appointment_id,
        )
        if provider_appointment_conflicts:
            return "appointment_conflict"
        if room_id is not None:
            room_appointment_conflicts = self._appointments.list_blocking_in_range(
                tenant_id,
                range_start=operational_start,
                range_end=operational_end,
                room_id=room_id,
                exclude_appointment_id=exclude_appointment_id,
            )
            if room_appointment_conflicts:
                return "appointment_conflict"

            room = self._rooms.get_by_id(tenant_id, room_id)
            if room is None or room.status != ClinicRoomStatus.ACTIVE:
                return "room_unavailable"
            room_blocks = self._blocks.list_in_range(
                tenant_id, range_start=operational_start, range_end=operational_end, room_id=room_id
            )
            if room_blocks:
                return "room_unavailable"

        provider_blocks = self._blocks.list_in_range(
            tenant_id,
            range_start=operational_start,
            range_end=operational_end,
            provider_user_id=provider_user_id,
        )
        if provider_blocks:
            return "blocked_period"

        tz = resolve_timezone(tz_name)
        local_date = starts_at.astimezone(tz).date()
        rules = self._schedules.list_active_for_provider_on_day(
            tenant_id, provider_user_id, local_date.weekday(), local_date
        )
        if not rules:
            return "outside_schedule"
        return "provider_unavailable"

    def _free_intervals_for_range(
        self,
        tenant_id: uuid.UUID,
        tz: ZoneInfo,
        *,
        provider_user_id: uuid.UUID,
        room_id: uuid.UUID | None,
        date_from: date,
        date_to: date,
        buffer_before: timedelta,
        buffer_after: timedelta,
        exclude_appointment_id: uuid.UUID | None = None,
    ) -> list[Interval]:
        range_start_utc = combine_local(date_from, datetime.min.time(), tz, on_nonexistent="skip")
        # date_from's midnight is virtually never a nonexistent DST
        # instant, but fail safe rather than silently narrowing the query.
        if range_start_utc is None:
            range_start_utc = datetime.combine(date_from, datetime.min.time(), tzinfo=UTC)
        range_end_date = date_to + timedelta(days=1)
        range_end_utc = combine_local(
            range_end_date, datetime.min.time(), tz, on_nonexistent="skip"
        )
        if range_end_utc is None:
            range_end_utc = datetime.combine(range_end_date, datetime.min.time(), tzinfo=UTC)

        blocking_appointments = self._appointments.list_blocking_in_range(
            tenant_id,
            range_start=range_start_utc,
            range_end=range_end_utc,
            provider_user_id=provider_user_id,
            room_id=room_id,
            exclude_appointment_id=exclude_appointment_id,
        )
        blocks = self._blocks.list_affecting_provider_or_room(
            tenant_id,
            range_start=range_start_utc,
            range_end=range_end_utc,
            provider_user_id=provider_user_id,
            room_id=room_id,
        )
        blocking_intervals: list[Interval] = [
            (a.starts_at, a.ends_at) for a in blocking_appointments
        ] + [(b.starts_at, b.ends_at) for b in blocks]

        schedule_intervals: list[Interval] = []
        current_date = date_from
        while current_date <= date_to:
            day_of_week = current_date.weekday()
            rules = self._schedules.list_active_for_provider_on_day(
                tenant_id, provider_user_id, day_of_week, current_date
            )
            for rule in rules:
                if rule.room_id is not None and room_id is not None and rule.room_id != room_id:
                    continue
                window_start = combine_local(
                    current_date, rule.start_time, tz, on_nonexistent="skip"
                )
                window_end = combine_local(current_date, rule.end_time, tz, on_nonexistent="skip")
                if window_start is None or window_end is None:
                    continue
                break_intervals: list[Interval] = []
                for schedule_break in self._schedules.list_breaks(rule.id):
                    b_start = combine_local(
                        current_date, schedule_break.start_time, tz, on_nonexistent="skip"
                    )
                    b_end = combine_local(
                        current_date, schedule_break.end_time, tz, on_nonexistent="skip"
                    )
                    if b_start is not None and b_end is not None:
                        break_intervals.append((b_start, b_end))
                for free in subtract_intervals((window_start, window_end), break_intervals):
                    # Shrink the operational window by the service's own
                    # buffers so a generated bookable slot never leaves
                    # less than the required buffer time around it.
                    shrunk_start = free[0] + buffer_before
                    shrunk_end = free[1] - buffer_after
                    if shrunk_start < shrunk_end:
                        schedule_intervals.append((shrunk_start, shrunk_end))
            current_date += timedelta(days=1)

        return compute_free_intervals(schedule_intervals, blocking_intervals)
