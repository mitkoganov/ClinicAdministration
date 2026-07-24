import uuid
from datetime import date, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.provider_schedule import ProviderSchedule, ProviderScheduleStatus, ScheduleBreak


class ProviderScheduleRepository:
    """Owns `ScheduleBreak` management too (see task.md: "embed inside the
    schedule repository if that proves cleaner") - a break is never
    queried independently of its parent schedule, and `schedule_breaks`
    has no `tenant_id` of its own (tenant scoping is inherited through
    `schedule_id`), so keeping both in one repository avoids splitting a
    single logical "schedule + its breaks" write into two repositories
    that would each need to re-derive the same tenant check."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, tenant_id: uuid.UUID, schedule_id: uuid.UUID) -> ProviderSchedule | None:
        stmt = select(ProviderSchedule).where(
            ProviderSchedule.tenant_id == tenant_id, ProviderSchedule.id == schedule_id
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        provider_user_id: uuid.UUID | None = None,
        status: ProviderScheduleStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ProviderSchedule], int]:
        conditions = [ProviderSchedule.tenant_id == tenant_id]
        if provider_user_id is not None:
            conditions.append(ProviderSchedule.provider_user_id == provider_user_id)
        if status is not None:
            conditions.append(ProviderSchedule.status == status)

        total = self._db.execute(
            select(func.count()).select_from(ProviderSchedule).where(*conditions)
        ).scalar_one()
        stmt = (
            select(ProviderSchedule)
            .where(*conditions)
            .order_by(ProviderSchedule.day_of_week.asc(), ProviderSchedule.start_time.asc())
            .limit(limit)
            .offset(offset)
        )
        items = list(self._db.execute(stmt).scalars().all())
        return items, total

    def list_active_for_provider_on_day(
        self,
        tenant_id: uuid.UUID,
        provider_user_id: uuid.UUID,
        day_of_week: int,
        on_date: date,
    ) -> list[ProviderSchedule]:
        """Every ACTIVE rule for this provider/weekday whose effective
        date range covers `on_date` - the exact input the availability
        engine needs for one calendar day, pre-filtered server-side
        rather than pulling every rule and filtering in Python."""
        stmt = select(ProviderSchedule).where(
            ProviderSchedule.tenant_id == tenant_id,
            ProviderSchedule.provider_user_id == provider_user_id,
            ProviderSchedule.day_of_week == day_of_week,
            ProviderSchedule.status == ProviderScheduleStatus.ACTIVE,
            ProviderSchedule.effective_from <= on_date,
            (ProviderSchedule.effective_until.is_(None))
            | (ProviderSchedule.effective_until >= on_date),
        )
        return list(self._db.execute(stmt).scalars().all())

    def list_active_for_provider(
        self, tenant_id: uuid.UUID, provider_user_id: uuid.UUID
    ) -> list[ProviderSchedule]:
        """Every ACTIVE rule for this provider, regardless of weekday or
        date range - used by the overlap-rejection check, which must
        compare a NEW rule's own weekday/date-range/time-range against
        every OTHER active rule, not just the ones matching a specific
        calendar date."""
        stmt = select(ProviderSchedule).where(
            ProviderSchedule.tenant_id == tenant_id,
            ProviderSchedule.provider_user_id == provider_user_id,
            ProviderSchedule.status == ProviderScheduleStatus.ACTIVE,
        )
        return list(self._db.execute(stmt).scalars().all())

    def create(
        self,
        tenant_id: uuid.UUID,
        *,
        provider_user_id: uuid.UUID,
        day_of_week: int,
        start_time: time,
        end_time: time,
        effective_from: date,
        effective_until: date | None,
        room_id: uuid.UUID | None,
    ) -> ProviderSchedule:
        schedule = ProviderSchedule(
            tenant_id=tenant_id,
            provider_user_id=provider_user_id,
            day_of_week=day_of_week,
            start_time=start_time,
            end_time=end_time,
            effective_from=effective_from,
            effective_until=effective_until,
            room_id=room_id,
        )
        self._db.add(schedule)
        self._db.flush()
        return schedule

    def update(
        self,
        tenant_id: uuid.UUID,
        schedule_id: uuid.UUID,
        *,
        start_time: time | None = None,
        end_time: time | None = None,
        effective_from: date | None = None,
        effective_until: date | None = None,
        room_id: uuid.UUID | None = None,
        status: ProviderScheduleStatus | None = None,
    ) -> ProviderSchedule | None:
        schedule = self.get_by_id(tenant_id, schedule_id)
        if schedule is None:
            return None
        if start_time is not None:
            schedule.start_time = start_time
        if end_time is not None:
            schedule.end_time = end_time
        if effective_from is not None:
            schedule.effective_from = effective_from
        if effective_until is not None:
            schedule.effective_until = effective_until
        if room_id is not None:
            schedule.room_id = room_id
        if status is not None:
            schedule.status = status
        self._db.flush()
        return schedule

    # --- ScheduleBreak -----------------------------------------------

    def list_breaks(self, schedule_id: uuid.UUID) -> list[ScheduleBreak]:
        stmt = (
            select(ScheduleBreak)
            .where(ScheduleBreak.schedule_id == schedule_id)
            .order_by(ScheduleBreak.start_time.asc())
        )
        return list(self._db.execute(stmt).scalars().all())

    def replace_breaks(
        self, schedule_id: uuid.UUID, breaks: list[tuple[time, time, str | None]]
    ) -> list[ScheduleBreak]:
        """Atomic "delete all, insert the new set" - the schedule-plus-
        breaks payload is always submitted as one complete list (see
        tasks/current/task.md "Prefer atomic schedule + breaks update"),
        never a partial patch of individual breaks."""
        existing = self.list_breaks(schedule_id)
        for existing_break in existing:
            self._db.delete(existing_break)
        self._db.flush()

        created = []
        for start_time, end_time, label in breaks:
            new_break = ScheduleBreak(
                schedule_id=schedule_id, start_time=start_time, end_time=end_time, label=label
            )
            self._db.add(new_break)
            created.append(new_break)
        self._db.flush()
        return created
