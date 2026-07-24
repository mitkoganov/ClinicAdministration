"""Real-concurrency tests for the appointment overlap exclusion constraints.

These deliberately do NOT use the `db_session` fixture (a single connection
wrapped in one outer transaction + SAVEPOINT-restart, per
tests/conftest.py) - that fixture cannot exercise a genuine race between
two independent database connections. Instead, each test here opens TWO
real, independent `Session`/connection objects and races an actual
overlapping DB operation (insert or update) between them on separate
threads, committing for real against the disposable test database and
cleaning up explicitly afterwards (see `_cleanup`).

Both sessions are opened sequentially in the main thread BEFORE any
threads are spawned - opening two brand-new connections at the exact same
instant was found to hang for minutes in this sandboxed environment
(consistent with a connection-rate limit on the Docker port forward: a
single new connection opens in milliseconds, but two truly simultaneous
new connection attempts stall for ~130s before one succeeds). Sequential
connection setup sidesteps that infrastructure quirk without weakening
what is actually under test: the race itself (the overlapping
insert/update) still runs concurrently across two independent,
already-established connections/threads, never simulated sequentially.

The racing tests use a deterministic two-phase handshake rather than a
bare `threading.Barrier` release: thread A flushes its (uncommitted)
conflicting row/update and signals thread B; thread B's own conflicting
operation then genuinely blocks inside Postgres (a GiST exclusion
constraint that finds a conflicting UNCOMMITTED row waits for that
transaction to finish, the same mechanism a foreign key check uses), and
only then is thread A released to commit, unblocking B so it can observe
the failure."""

import threading
import time as time_module
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.models.appointment_service_type import AppointmentServiceType, ServiceTypeStatus
from app.models.clinic_room import ClinicRoom, ClinicRoomStatus
from app.models.membership import MembershipRole, MembershipStatus, TenantMembership
from app.models.provider_schedule import ProviderSchedule, ProviderScheduleStatus
from app.models.tenant import Tenant, TenantStatus
from app.repositories.appointment import AppointmentRepository
from tests.db_safety import get_test_database_url

pytestmark = pytest.mark.integration

_HANDSHAKE_WAIT_SECONDS = 30
_SETTLE_DELAY_SECONDS = 0.5


class _Fixture:
    def __init__(
        self, tenant_id, provider_user_id, second_provider_user_id, room_id, service_type_id
    ):
        self.tenant_id = tenant_id
        self.provider_user_id = provider_user_id
        self.second_provider_user_id = second_provider_user_id
        self.room_id = room_id
        self.service_type_id = service_type_id


def _setup(db_engine) -> _Fixture:
    session = Session(bind=db_engine)
    try:
        tenant = Tenant(
            name="Concurrency Tenant",
            slug=f"concurrency-{uuid.uuid4().hex[:8]}",
            status=TenantStatus.ACTIVE,
        )
        session.add(tenant)
        session.flush()

        provider_user_id = uuid.uuid4()
        second_provider_user_id = uuid.uuid4()
        session.add_all(
            [
                TenantMembership(
                    tenant_id=tenant.id,
                    user_id=provider_user_id,
                    role=MembershipRole.OWNER,
                    status=MembershipStatus.ACTIVE,
                ),
                TenantMembership(
                    tenant_id=tenant.id,
                    user_id=second_provider_user_id,
                    role=MembershipRole.MANAGER,
                    status=MembershipStatus.ACTIVE,
                ),
            ]
        )

        room = ClinicRoom(
            tenant_id=tenant.id,
            name="Room",
            code="R1",
            description=None,
            status=ClinicRoomStatus.ACTIVE,
        )
        session.add(room)
        session.flush()

        service_type = AppointmentServiceType(
            tenant_id=tenant.id,
            name="General",
            code="GEN",
            description=None,
            default_duration_minutes=30,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            status=ServiceTypeStatus.ACTIVE,
        )
        session.add(service_type)
        session.flush()

        for provider in (provider_user_id, second_provider_user_id):
            for day_of_week in range(7):
                session.add(
                    ProviderSchedule(
                        tenant_id=tenant.id,
                        provider_user_id=provider,
                        day_of_week=day_of_week,
                        start_time=time(0, 0),
                        end_time=time(23, 59),
                        effective_from=date(2020, 1, 1),
                        effective_until=None,
                        room_id=room.id,
                        status=ProviderScheduleStatus.ACTIVE,
                    )
                )
        session.flush()
        session.commit()

        return _Fixture(
            tenant_id=tenant.id,
            provider_user_id=provider_user_id,
            second_provider_user_id=second_provider_user_id,
            room_id=room.id,
            service_type_id=service_type.id,
        )
    finally:
        session.close()


def _cleanup(db_engine, tenant_id: uuid.UUID) -> None:
    session = Session(bind=db_engine)
    try:
        session.query(Appointment).filter(Appointment.tenant_id == tenant_id).delete()
        session.query(ProviderSchedule).filter(ProviderSchedule.tenant_id == tenant_id).delete()
        session.query(AppointmentServiceType).filter(
            AppointmentServiceType.tenant_id == tenant_id
        ).delete()
        session.query(ClinicRoom).filter(ClinicRoom.tenant_id == tenant_id).delete()
        session.query(TenantMembership).filter(TenantMembership.tenant_id == tenant_id).delete()
        session.query(Tenant).filter(Tenant.id == tenant_id).delete()
        session.commit()
    finally:
        session.close()


def _open_bounded_sessions(count: int) -> list[Session]:
    """Opens `count` fully independent sessions (own engine/connection
    each), SEQUENTIALLY - see this module's docstring for why simultaneous
    new-connection attempts must be avoided here. Each connection gets its
    own `lock_timeout` so a bug in a test's handshake fails fast with a
    clear error instead of hanging."""
    sessions = []
    for _ in range(count):
        engine = create_engine(get_test_database_url(), future=True)
        session = Session(bind=engine)
        session.execute(text("SET lock_timeout = '15s'"))
        session.info["standalone_engine"] = engine
        sessions.append(session)
    return sessions


def _dispose_bounded_session(session: Session) -> None:
    session.close()
    engine = session.info.get("standalone_engine")
    if engine is not None:
        engine.dispose()


def _race_two_inserts(fixture: _Fixture, *, provider_ids, room_ids, patient_names):
    """Thread A flushes an appointment row (uncommitted) and signals thread
    B; thread B's overlapping insert then blocks inside Postgres until A
    commits (released after a short settle delay so B is genuinely
    waiting, not merely about to start). Returns (result_a, result_b)
    tuples of ("ok", appointment_id) or ("error", exception)."""
    session_a, session_b = _open_bounded_sessions(2)
    starts_at = datetime.now(UTC) + timedelta(hours=2500)
    ends_at = starts_at + timedelta(minutes=30)
    a_flushed = threading.Event()
    release_a = threading.Event()
    results: list[object] = [None, None]

    def _thread_a() -> None:
        try:
            repo = AppointmentRepository(session_a)
            try:
                appointment = repo.create(
                    fixture.tenant_id,
                    provider_user_id=provider_ids[0],
                    room_id=room_ids[0],
                    service_type_id=fixture.service_type_id,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    patient_display_name=patient_names[0],
                    patient_phone=None,
                    patient_email=None,
                    notes=None,
                    created_by_user_id=provider_ids[0],
                )
                results[0] = ("ok", appointment.id)
            except Exception as exc:  # noqa: BLE001 - captured for cross-thread assertion
                results[0] = ("error", exc)
            finally:
                a_flushed.set()
            release_a.wait(timeout=_HANDSHAKE_WAIT_SECONDS)
            if results[0][0] == "ok":
                session_a.commit()
            else:
                session_a.rollback()
        finally:
            _dispose_bounded_session(session_a)

    def _thread_b() -> None:
        assert a_flushed.wait(timeout=_HANDSHAKE_WAIT_SECONDS), "thread A never flushed"
        try:
            repo = AppointmentRepository(session_b)
            try:
                appointment = repo.create(
                    fixture.tenant_id,
                    provider_user_id=provider_ids[1],
                    room_id=room_ids[1],
                    service_type_id=fixture.service_type_id,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    patient_display_name=patient_names[1],
                    patient_phone=None,
                    patient_email=None,
                    notes=None,
                    created_by_user_id=provider_ids[1],
                )
                session_b.commit()
                results[1] = ("ok", appointment.id)
            except Exception as exc:  # noqa: BLE001
                session_b.rollback()
                results[1] = ("error", exc)
        finally:
            _dispose_bounded_session(session_b)

    thread_a = threading.Thread(target=_thread_a)
    thread_b = threading.Thread(target=_thread_b)
    thread_a.start()
    thread_b.start()
    assert a_flushed.wait(timeout=_HANDSHAKE_WAIT_SECONDS), "thread A never flushed"
    # Give thread B time to actually reach (and block inside) its own
    # conflicting insert before thread A is allowed to commit.
    time_module.sleep(_SETTLE_DELAY_SECONDS)
    release_a.set()
    thread_a.join(timeout=_HANDSHAKE_WAIT_SECONDS)
    thread_b.join(timeout=_HANDSHAKE_WAIT_SECONDS)
    return results


def test_concurrent_creates_for_same_provider_only_one_succeeds(db_engine):
    fixture = _setup(db_engine)
    try:
        results = _race_two_inserts(
            fixture,
            provider_ids=[fixture.provider_user_id, fixture.provider_user_id],
            room_ids=[None, None],
            patient_names=["Patient A", "Patient B"],
        )
        outcomes = [r[0] for r in results]
        assert outcomes.count("ok") == 1
        assert outcomes.count("error") == 1
    finally:
        _cleanup(db_engine, fixture.tenant_id)


def test_concurrent_creates_for_same_room_different_providers_only_one_succeeds(db_engine):
    fixture = _setup(db_engine)
    try:
        results = _race_two_inserts(
            fixture,
            provider_ids=[fixture.provider_user_id, fixture.second_provider_user_id],
            room_ids=[fixture.room_id, fixture.room_id],
            patient_names=["Patient A", "Patient B"],
        )
        outcomes = [r[0] for r in results]
        assert outcomes.count("ok") == 1
        assert outcomes.count("error") == 1
    finally:
        _cleanup(db_engine, fixture.tenant_id)


def test_concurrent_creates_for_different_providers_different_rooms_both_succeed(db_engine):
    # No shared provider or room - the two inserts never conflict, so a
    # bare simultaneous release (no handshake needed) is safe here.
    fixture = _setup(db_engine)
    session_0, session_1 = _open_bounded_sessions(2)
    sessions = [session_0, session_1]
    barrier = threading.Barrier(2)
    results: list[object] = [None, None]
    provider_ids = [fixture.provider_user_id, fixture.second_provider_user_id]
    starts_at = datetime.now(UTC) + timedelta(hours=2600)
    ends_at = starts_at + timedelta(minutes=30)

    def _worker(index: int) -> None:
        session = sessions[index]
        try:
            repo = AppointmentRepository(session)
            try:
                barrier.wait(timeout=_HANDSHAKE_WAIT_SECONDS)
                appointment = repo.create(
                    fixture.tenant_id,
                    provider_user_id=provider_ids[index],
                    room_id=None,
                    service_type_id=fixture.service_type_id,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    patient_display_name=f"Patient {index}",
                    patient_phone=None,
                    patient_email=None,
                    notes=None,
                    created_by_user_id=provider_ids[index],
                )
                session.commit()
                results[index] = ("ok", appointment.id)
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                results[index] = ("error", exc)
        finally:
            _dispose_bounded_session(session)

    try:
        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=_HANDSHAKE_WAIT_SECONDS)
        outcomes = [r[0] for r in results]
        assert outcomes.count("ok") == 2
    finally:
        _cleanup(db_engine, fixture.tenant_id)


def test_concurrent_reschedule_race_only_one_wins(db_engine):
    fixture = _setup(db_engine)
    try:
        setup_session = Session(bind=db_engine)
        try:
            repo = AppointmentRepository(setup_session)
            base_start = datetime.now(UTC) + timedelta(hours=3000)
            appointment_a = repo.create(
                fixture.tenant_id,
                provider_user_id=fixture.provider_user_id,
                room_id=None,
                service_type_id=fixture.service_type_id,
                starts_at=base_start,
                ends_at=base_start + timedelta(minutes=30),
                patient_display_name="Movable appointment",
                patient_phone=None,
                patient_email=None,
                notes=None,
                created_by_user_id=fixture.provider_user_id,
            )
            appointment_b = repo.create(
                fixture.tenant_id,
                provider_user_id=fixture.provider_user_id,
                room_id=None,
                service_type_id=fixture.service_type_id,
                starts_at=base_start + timedelta(hours=5),
                ends_at=base_start + timedelta(hours=5, minutes=30),
                patient_display_name="Other appointment",
                patient_phone=None,
                patient_email=None,
                notes=None,
                created_by_user_id=fixture.provider_user_id,
            )
            setup_session.commit()
            appointment_a_id = appointment_a.id
            appointment_b_id = appointment_b.id
        finally:
            setup_session.close()

        target_start = base_start + timedelta(hours=10)
        target_end = target_start + timedelta(minutes=30)
        session_a, session_b = _open_bounded_sessions(2)
        a_flushed = threading.Event()
        release_a = threading.Event()
        results: list[object] = [None, None]

        def _reschedule_a() -> None:
            try:
                repo = AppointmentRepository(session_a)
                try:
                    updated = repo.update_with_version(
                        fixture.tenant_id,
                        appointment_a_id,
                        1,
                        updated_by_user_id=fixture.provider_user_id,
                        values={"starts_at": target_start, "ends_at": target_end},
                    )
                    results[0] = ("ok", updated.id) if updated is not None else ("error", None)
                except Exception as exc:  # noqa: BLE001
                    results[0] = ("error", exc)
                finally:
                    a_flushed.set()
                release_a.wait(timeout=_HANDSHAKE_WAIT_SECONDS)
                if results[0][0] == "ok":
                    session_a.commit()
                else:
                    session_a.rollback()
            finally:
                _dispose_bounded_session(session_a)

        def _reschedule_b() -> None:
            assert a_flushed.wait(timeout=_HANDSHAKE_WAIT_SECONDS), "thread A never flushed"
            try:
                repo = AppointmentRepository(session_b)
                try:
                    updated = repo.update_with_version(
                        fixture.tenant_id,
                        appointment_b_id,
                        1,
                        updated_by_user_id=fixture.provider_user_id,
                        values={"starts_at": target_start, "ends_at": target_end},
                    )
                    session_b.commit()
                    results[1] = ("ok", updated.id) if updated is not None else ("error", None)
                except Exception as exc:  # noqa: BLE001
                    session_b.rollback()
                    results[1] = ("error", exc)
            finally:
                _dispose_bounded_session(session_b)

        thread_a = threading.Thread(target=_reschedule_a)
        thread_b = threading.Thread(target=_reschedule_b)
        thread_a.start()
        thread_b.start()
        assert a_flushed.wait(timeout=_HANDSHAKE_WAIT_SECONDS), "thread A never flushed"
        time_module.sleep(_SETTLE_DELAY_SECONDS)
        release_a.set()
        thread_a.join(timeout=_HANDSHAKE_WAIT_SECONDS)
        thread_b.join(timeout=_HANDSHAKE_WAIT_SECONDS)

        outcomes = [r[0] for r in results]
        assert outcomes.count("ok") == 1
        assert outcomes.count("error") == 1
    finally:
        _cleanup(db_engine, fixture.tenant_id)
