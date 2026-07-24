"""MED-005 test fixtures - built on top of `tests.factories`' `tenancy`
fixture (real tenants + role-covering users), never a separate identity
system. `owner_a` doubles as the default "provider" in these fixtures
since it already has an ACTIVE OWNER membership in `tenant_a` - the exact
precondition `ScheduleService`/`AppointmentService` require."""

from dataclasses import dataclass
from datetime import date, time

import pytest
from sqlalchemy.orm import Session

from app.models.appointment_service_type import AppointmentServiceType, ServiceTypeStatus
from app.models.clinic_room import ClinicRoom, ClinicRoomStatus
from app.models.provider_schedule import ProviderSchedule, ProviderScheduleStatus
from tests.factories import Tenancy


@dataclass
class CalendarTenancy:
    tenancy: Tenancy
    room_a: ClinicRoom
    service_type_a: AppointmentServiceType
    # Wide-open (all 7 weekdays, nearly-all-day) schedules for owner_a in
    # tenant_a - deliberately NOT a narrow "9-to-5" window, so ordinary
    # integration tests (which run against the real current wall-clock
    # time, not a mocked clock) never fail merely because "today" or
    # "tomorrow" happens to be a weekday the schedule doesn't cover.
    # DST/day-of-week-specific edge cases are covered by
    # tests/unit/test_scheduling_time.py's pure unit tests instead, where
    # the "current date" is fully controlled.
    schedules_a: list[ProviderSchedule]


@pytest.fixture
def calendar_tenancy(db_session: Session, tenancy: Tenancy) -> CalendarTenancy:
    db = db_session

    room_a = ClinicRoom(
        tenant_id=tenancy.tenant_a.id,
        name="Room 1",
        code="R1",
        description="Primary exam room",
        status=ClinicRoomStatus.ACTIVE,
    )
    db.add(room_a)
    db.flush()

    service_type_a = AppointmentServiceType(
        tenant_id=tenancy.tenant_a.id,
        name="General Checkup",
        code="GEN",
        description="Standard appointment",
        default_duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        status=ServiceTypeStatus.ACTIVE,
    )
    db.add(service_type_a)
    db.flush()

    schedules = []
    for day_of_week in range(7):
        schedule = ProviderSchedule(
            tenant_id=tenancy.tenant_a.id,
            provider_user_id=tenancy.owner_a,
            day_of_week=day_of_week,
            start_time=time(0, 0),
            end_time=time(23, 59),
            effective_from=date(2020, 1, 1),
            effective_until=None,
            room_id=room_a.id,
            status=ProviderScheduleStatus.ACTIVE,
        )
        db.add(schedule)
        schedules.append(schedule)
    db.flush()

    return CalendarTenancy(
        tenancy=tenancy,
        room_a=room_a,
        service_type_a=service_type_a,
        schedules_a=schedules,
    )
