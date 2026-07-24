import uuid
from datetime import date, time

from sqlalchemy.orm import Session

from app.core.audit import AuditEvent, AuditOutcome, emit_audit_event
from app.core.authorization import CALENDAR_CONFIG_ROLES, CALENDAR_READ_ROLES, require_role
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.tenant_context import TenantContext
from app.models.clinic_room import ClinicRoomStatus
from app.models.membership import MembershipStatus
from app.models.provider_schedule import ProviderSchedule, ProviderScheduleStatus, ScheduleBreak
from app.repositories.clinic_room import ClinicRoomRepository
from app.repositories.membership import MembershipRepository
from app.repositories.provider_schedule import ProviderScheduleRepository

_RESOURCE_TYPE = "provider_schedule"

# Module-level alias, not a bare `list[...]` used directly inside the
# class body: ScheduleService defines a method literally named `list`,
# which - once that `def` statement executes - shadows the builtin
# `list` for the REST of the class body's own execution (class bodies
# execute top-to-bottom like a script, and annotations are evaluated at
# `def` time using that same namespace). Any later method's own
# `list[...]` annotation would otherwise resolve to the `list` METHOD
# object instead of the builtin type and fail with "'function' object is
# not subscriptable".
BreakInput = tuple[time, time, str | None]
BreaksInput = list[BreakInput]
ScheduleBreaksResult = list[ScheduleBreak]


def _date_ranges_intersect(
    a_from: date, a_until: date | None, b_from: date, b_until: date | None
) -> bool:
    a_end = a_until or date.max
    b_end = b_until or date.max
    return a_from <= b_end and b_from <= a_end


def _time_ranges_intersect(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    return a_start < b_end and b_start < a_end


class ScheduleService:
    """Recurring-schedule overlap rejection is enforced HERE (service
    layer), not by a database constraint - see
    app.models.provider_schedule.ProviderSchedule's docstring for why a
    DB-level guarantee was judged not worth the complexity for this
    foundation slice. Appointment-vs-appointment double-booking keeps its
    mandatory DB-level protection regardless (see
    app.services.appointment_service)."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = ProviderScheduleRepository(db)
        self._rooms = ClinicRoomRepository(db)
        self._memberships = MembershipRepository(db)

    def list(
        self,
        context: TenantContext,
        *,
        provider_user_id: uuid.UUID | None,
        status: ProviderScheduleStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ProviderSchedule], int]:
        require_role(context, CALENDAR_READ_ROLES)
        return self._repo.list_by_tenant(
            context.tenant_id,
            provider_user_id=provider_user_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    def get(self, context: TenantContext, schedule_id: uuid.UUID) -> ProviderSchedule:
        require_role(context, CALENDAR_READ_ROLES)
        schedule = self._repo.get_by_id(context.tenant_id, schedule_id)
        if schedule is None:
            raise NotFoundError()
        return schedule

    def get_breaks(self, context: TenantContext, schedule_id: uuid.UUID) -> ScheduleBreaksResult:
        require_role(context, CALENDAR_READ_ROLES)
        schedule = self._repo.get_by_id(context.tenant_id, schedule_id)
        if schedule is None:
            raise NotFoundError()
        return self._repo.list_breaks(schedule_id)

    def create(
        self,
        context: TenantContext,
        *,
        provider_user_id: uuid.UUID,
        day_of_week: int,
        start_time: time,
        end_time: time,
        effective_from: date,
        effective_until: date | None,
        room_id: uuid.UUID | None,
        breaks: BreaksInput,
    ) -> ProviderSchedule:
        try:
            require_role(context, CALENDAR_CONFIG_ROLES)
            self._validate_provider_and_room(context, provider_user_id, room_id)
            self._validate_no_overlap(
                context,
                provider_user_id,
                day_of_week,
                start_time,
                end_time,
                effective_from,
                effective_until,
                exclude_schedule_id=None,
            )
            self._validate_breaks(start_time, end_time, breaks)
        except (ForbiddenError, ConflictError, NotFoundError):
            self._audit(context, "calendar.schedule_created", AuditOutcome.REJECTED)
            raise

        schedule = self._repo.create(
            context.tenant_id,
            provider_user_id=provider_user_id,
            day_of_week=day_of_week,
            start_time=start_time,
            end_time=end_time,
            effective_from=effective_from,
            effective_until=effective_until,
            room_id=room_id,
        )
        if breaks:
            self._repo.replace_breaks(schedule.id, breaks)

        self._db.commit()
        self._audit(context, "calendar.schedule_created", AuditOutcome.SUCCESS, schedule.id)
        return schedule

    def update(
        self,
        context: TenantContext,
        schedule_id: uuid.UUID,
        *,
        start_time: time | None,
        end_time: time | None,
        effective_from: date | None,
        effective_until: date | None,
        room_id: uuid.UUID | None,
        breaks: BreaksInput | None,
    ) -> ProviderSchedule:
        try:
            require_role(context, CALENDAR_CONFIG_ROLES)
        except ForbiddenError:
            self._audit(context, "calendar.schedule_updated", AuditOutcome.REJECTED, schedule_id)
            raise

        existing = self._repo.get_by_id(context.tenant_id, schedule_id)
        if existing is None:
            self._audit(context, "calendar.schedule_updated", AuditOutcome.REJECTED, schedule_id)
            raise NotFoundError()

        new_start = start_time if start_time is not None else existing.start_time
        new_end = end_time if end_time is not None else existing.end_time
        new_from = effective_from if effective_from is not None else existing.effective_from
        new_until = effective_until if effective_until is not None else existing.effective_until

        try:
            if room_id is not None:
                self._validate_provider_and_room(context, existing.provider_user_id, room_id)
            if new_start >= new_end:
                raise ConflictError("start_time must be before end_time.")
            self._validate_no_overlap(
                context,
                existing.provider_user_id,
                existing.day_of_week,
                new_start,
                new_end,
                new_from,
                new_until,
                exclude_schedule_id=existing.id,
            )
            if breaks is not None:
                self._validate_breaks(new_start, new_end, breaks)
        except (ForbiddenError, ConflictError, NotFoundError):
            self._audit(context, "calendar.schedule_updated", AuditOutcome.REJECTED, schedule_id)
            raise

        schedule = self._repo.update(
            context.tenant_id,
            schedule_id,
            start_time=start_time,
            end_time=end_time,
            effective_from=effective_from,
            effective_until=effective_until,
            room_id=room_id,
        )
        assert schedule is not None
        if breaks is not None:
            self._repo.replace_breaks(schedule.id, breaks)

        self._db.commit()
        self._audit(context, "calendar.schedule_updated", AuditOutcome.SUCCESS, schedule.id)
        return schedule

    def deactivate(self, context: TenantContext, schedule_id: uuid.UUID) -> ProviderSchedule:
        try:
            require_role(context, CALENDAR_CONFIG_ROLES)
        except ForbiddenError:
            self._audit(
                context, "calendar.schedule_deactivated", AuditOutcome.REJECTED, schedule_id
            )
            raise

        schedule = self._repo.update(
            context.tenant_id, schedule_id, status=ProviderScheduleStatus.INACTIVE
        )
        if schedule is None:
            self._audit(
                context, "calendar.schedule_deactivated", AuditOutcome.REJECTED, schedule_id
            )
            raise NotFoundError()

        self._db.commit()
        self._audit(context, "calendar.schedule_deactivated", AuditOutcome.SUCCESS, schedule.id)
        return schedule

    def _validate_provider_and_room(
        self, context: TenantContext, provider_user_id: uuid.UUID, room_id: uuid.UUID | None
    ) -> None:
        membership = self._memberships.get_membership(context.tenant_id, provider_user_id)
        if membership is None or membership.status != MembershipStatus.ACTIVE:
            raise NotFoundError("Provider has no active membership in this clinic.")
        if room_id is not None:
            room = self._rooms.get_by_id(context.tenant_id, room_id)
            if room is None:
                raise NotFoundError("Room not found in this clinic.")
            if room.status != ClinicRoomStatus.ACTIVE:
                raise ConflictError("Room is not active.")

    def _validate_no_overlap(
        self,
        context: TenantContext,
        provider_user_id: uuid.UUID,
        day_of_week: int,
        start_time: time,
        end_time: time,
        effective_from: date,
        effective_until: date | None,
        *,
        exclude_schedule_id: uuid.UUID | None,
    ) -> None:
        existing_rules = self._repo.list_active_for_provider(context.tenant_id, provider_user_id)
        for rule in existing_rules:
            if exclude_schedule_id is not None and rule.id == exclude_schedule_id:
                continue
            if rule.day_of_week != day_of_week:
                continue
            if not _date_ranges_intersect(
                effective_from, effective_until, rule.effective_from, rule.effective_until
            ):
                continue
            if _time_ranges_intersect(start_time, end_time, rule.start_time, rule.end_time):
                raise ConflictError(
                    "This provider already has an overlapping active schedule rule for this "
                    "weekday and date range."
                )

    def _validate_breaks(
        self, schedule_start: time, schedule_end: time, breaks: BreaksInput
    ) -> None:
        for start_time, end_time, _label in breaks:
            if start_time >= end_time:
                raise ConflictError("Break start_time must be before end_time.")
            if start_time < schedule_start or end_time > schedule_end:
                raise ConflictError("Break must fall within the schedule's own time range.")
        sorted_breaks = sorted(breaks, key=lambda b: b[0])
        for i in range(len(sorted_breaks) - 1):
            if sorted_breaks[i][1] > sorted_breaks[i + 1][0]:
                raise ConflictError("Breaks on the same schedule must not overlap.")

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
