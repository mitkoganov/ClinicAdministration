import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.integration.conftest import dev_headers

pytestmark = pytest.mark.integration

APPOINTMENTS_URL = "/api/v1/appointments"


def _near_future_window(minutes_ahead: int = 10, duration_minutes: int = 30):
    starts_at = datetime.now(UTC) + timedelta(minutes=minutes_ahead)
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    return starts_at, ends_at


def _create_payload(calendar_tenancy, *, minutes_ahead=10, provider_user_id=None, room_id=None):
    starts_at, ends_at = _near_future_window(minutes_ahead)
    return {
        "provider_user_id": str(provider_user_id or calendar_tenancy.tenancy.owner_a),
        "room_id": str(room_id) if room_id else str(calendar_tenancy.room_a.id),
        "service_type_id": str(calendar_tenancy.service_type_a.id),
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "patient_display_name": "Ivan Ivanov",
        "patient_phone": "+359 88 123 4567",
        "patient_email": "ivan@example.com",
    }


def _create(client, calendar_tenancy, actor, **kwargs):
    tenancy = calendar_tenancy.tenancy
    return client.post(
        APPOINTMENTS_URL,
        json=_create_payload(calendar_tenancy, **kwargs),
        headers=dev_headers(actor, tenancy.tenant_a.id),
    )


def test_operator_can_create_appointment(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = _create(client, calendar_tenancy, tenancy.operator_a)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "scheduled"
    assert body["patient_phone"] == "+359881234567"
    assert body["version"] == 1


def test_auditor_cannot_create_appointment(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = _create(client, calendar_tenancy, tenancy.auditor_a)
    assert response.status_code == 403


def test_auditor_sees_redacted_summary_not_contact_info(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=15).json()

    response = client.get(
        f"{APPOINTMENTS_URL}/{created['id']}",
        headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert "patient_phone" not in body
    assert "patient_email" not in body
    assert body["status"] == "scheduled"


def test_sequential_provider_overlap_is_rejected_by_pre_check(client, calendar_tenancy):
    # A sequential double-booking is caught by AvailabilityService's
    # pre-check (is_interval_free) before ever reaching the DB insert.
    # AvailabilityService.diagnose_unavailable_reason classifies this as
    # "appointment_conflict" too (an existing blocking Appointment row
    # overlaps) - the same code the DB exclusion constraint itself would
    # raise for a genuine concurrent race (see
    # tests/integration/test_appointment_concurrency.py), so a caller
    # sees one consistent code regardless of which layer caught it.
    tenancy = calendar_tenancy.tenancy
    starts_at, ends_at = _near_future_window(minutes_ahead=60)
    payload = {
        "provider_user_id": str(tenancy.owner_a),
        "room_id": None,
        "service_type_id": str(calendar_tenancy.service_type_a.id),
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "patient_display_name": "Patient One",
    }
    first = client.post(
        APPOINTMENTS_URL, json=payload, headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id)
    )
    assert first.status_code == 200

    overlapping = dict(payload)
    overlapping["patient_display_name"] = "Patient Two"
    second = client.post(
        APPOINTMENTS_URL,
        json=overlapping,
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert second.status_code == 409
    assert second.json()["code"] == "appointment_conflict"


def test_provider_with_no_schedule_at_all_gets_outside_schedule_code(client, calendar_tenancy):
    # manager_a has an active membership in calendar_tenancy but no
    # ProviderSchedule row was ever created for them - distinct from
    # "provider_unavailable" (a rule exists for this day, but this
    # specific time isn't covered by it).
    tenancy = calendar_tenancy.tenancy
    response = _create(
        client, calendar_tenancy, tenancy.owner_a, provider_user_id=tenancy.manager_a
    )
    assert response.status_code == 409
    assert response.json()["code"] == "outside_schedule"


def test_narrow_schedule_window_gets_provider_unavailable_code(client, calendar_tenancy):
    from zoneinfo import ZoneInfo

    tenancy = calendar_tenancy.tenancy
    clinic = client.get(
        "/api/v1/clinic", headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id)
    ).json()
    tz = ZoneInfo(clinic["timezone"])

    # A date safely in the future (avoids any "past time" rejection),
    # with a schedule rule covering only its own weekday, 09:00-09:30
    # LOCAL tenant time.
    target_local_date = (datetime.now(tz) + timedelta(days=14)).date()
    created_schedule = client.post(
        "/api/v1/provider-schedules",
        json={
            "provider_user_id": str(tenancy.auditor_a),
            "day_of_week": target_local_date.weekday(),
            "start_time": "09:00:00",
            "end_time": "09:30:00",
            "effective_from": "2020-01-01",
            "effective_until": None,
            "room_id": None,
            "breaks": [],
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert created_schedule.status_code == 200

    # 14:00 LOCAL time on that date falls outside the 09:00-09:30 window,
    # even though a rule exists for that weekday.
    outside_window_local = datetime(
        target_local_date.year, target_local_date.month, target_local_date.day, 14, 0, tzinfo=tz
    )
    starts_at = outside_window_local.astimezone(UTC)
    response = client.post(
        APPOINTMENTS_URL,
        json={
            "provider_user_id": str(tenancy.auditor_a),
            "room_id": None,
            "service_type_id": str(calendar_tenancy.service_type_a.id),
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(minutes=30)).isoformat(),
            "patient_display_name": "Narrow window patient",
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "provider_unavailable"


def test_room_overlap_constraint_rejects_overlap_across_providers(
    client, calendar_tenancy, db_session
):
    from datetime import date, time

    from app.models.provider_schedule import ProviderSchedule, ProviderScheduleStatus

    tenancy = calendar_tenancy.tenancy
    db_session.add(
        ProviderSchedule(
            tenant_id=tenancy.tenant_a.id,
            provider_user_id=tenancy.manager_a,
            day_of_week=date.today().weekday(),
            start_time=time(0, 0),
            end_time=time(23, 59),
            effective_from=date(2020, 1, 1),
            effective_until=None,
            room_id=calendar_tenancy.room_a.id,
            status=ProviderScheduleStatus.ACTIVE,
        )
    )
    db_session.flush()

    starts_at, ends_at = _near_future_window(minutes_ahead=150)
    owner_payload = {
        "provider_user_id": str(tenancy.owner_a),
        "room_id": str(calendar_tenancy.room_a.id),
        "service_type_id": str(calendar_tenancy.service_type_a.id),
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "patient_display_name": "Owner's patient",
    }
    first = client.post(
        APPOINTMENTS_URL,
        json=owner_payload,
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert first.status_code == 200

    manager_payload = dict(owner_payload)
    manager_payload["provider_user_id"] = str(tenancy.manager_a)
    manager_payload["patient_display_name"] = "Manager's patient"
    second = client.post(
        APPOINTMENTS_URL,
        json=manager_payload,
        headers=dev_headers(tenancy.manager_a, tenancy.tenant_a.id),
    )
    assert second.status_code == 409
    assert second.json()["code"] == "appointment_conflict"


def test_adjacent_appointments_in_same_room_do_not_conflict(client, calendar_tenancy):
    # The overlap check is a half-open interval ([start, end)) - two
    # bookings that only touch at a boundary must both succeed. Actual
    # room-overlap rejection is exercised by the DB exclusion constraint
    # test below (test_room_overlap_constraint_rejects_overlap), which uses
    # a second provider with its own schedule so the times genuinely
    # overlap without also depending on the provider-overlap constraint.
    tenancy = calendar_tenancy.tenancy
    starts_at, ends_at = _near_future_window(minutes_ahead=120)
    payload = {
        "provider_user_id": str(tenancy.owner_a),
        "room_id": str(calendar_tenancy.room_a.id),
        "service_type_id": str(calendar_tenancy.service_type_a.id),
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "patient_display_name": "Patient One",
    }
    first = client.post(
        APPOINTMENTS_URL, json=payload, headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id)
    )
    assert first.status_code == 200

    adjacent = dict(payload)
    adjacent["starts_at"] = ends_at.isoformat()
    adjacent["ends_at"] = (ends_at + timedelta(minutes=30)).isoformat()
    adjacent["patient_display_name"] = "Patient Two"
    second = client.post(
        APPOINTMENTS_URL,
        json=adjacent,
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert second.status_code == 200


def test_cancelled_appointment_frees_its_slot(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    starts_at, ends_at = _near_future_window(minutes_ahead=200)
    payload = {
        "provider_user_id": str(tenancy.owner_a),
        "room_id": None,
        "service_type_id": str(calendar_tenancy.service_type_a.id),
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "patient_display_name": "Patient One",
    }
    created = client.post(
        APPOINTMENTS_URL, json=payload, headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id)
    ).json()

    cancel_response = client.post(
        f"{APPOINTMENTS_URL}/{created['id']}/cancel",
        json={"expected_version": created["version"], "reason": "Patient requested"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    retry = client.post(
        APPOINTMENTS_URL, json=payload, headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id)
    )
    assert retry.status_code == 200


def test_cancel_is_idempotent(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=250).json()

    first_cancel = client.post(
        f"{APPOINTMENTS_URL}/{created['id']}/cancel",
        json={"expected_version": created["version"], "reason": "First cancel"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert first_cancel.status_code == 200

    second_cancel = client.post(
        f"{APPOINTMENTS_URL}/{created['id']}/cancel",
        json={"expected_version": first_cancel.json()["version"], "reason": "Second attempt"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert second_cancel.status_code == 200
    assert second_cancel.json()["status"] == "cancelled"


def test_reschedule_moves_appointment_and_bumps_version(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=300).json()
    new_start, new_end = _near_future_window(minutes_ahead=320)

    response = client.post(
        f"{APPOINTMENTS_URL}/{created['id']}/reschedule",
        json={
            "expected_version": created["version"],
            "starts_at": new_start.isoformat(),
            "ends_at": new_end.isoformat(),
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 2


def test_stale_version_reschedule_is_rejected(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=400).json()
    new_start, new_end = _near_future_window(minutes_ahead=420)

    response = client.post(
        f"{APPOINTMENTS_URL}/{created['id']}/reschedule",
        json={
            "expected_version": created["version"] + 5,
            "starts_at": new_start.isoformat(),
            "ends_at": new_end.isoformat(),
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "stale_version"


def test_confirm_transitions_scheduled_to_confirmed(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=800).json()

    confirmed = client.post(
        f"{APPOINTMENTS_URL}/{created['id']}/confirm",
        json={"expected_version": created["version"]},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"


def test_complete_rejected_before_start_time_then_allowed_after(
    client, calendar_tenancy, db_session
):
    from app.models.appointment import Appointment

    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=900).json()

    too_early = client.post(
        f"{APPOINTMENTS_URL}/{created['id']}/complete",
        json={"expected_version": created["version"]},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert too_early.status_code == 409

    row = db_session.get(Appointment, uuid.UUID(created["id"]))
    row.starts_at = datetime.now(UTC) - timedelta(minutes=5)
    db_session.flush()

    completed = client.post(
        f"{APPOINTMENTS_URL}/{created['id']}/complete",
        json={"expected_version": created["version"]},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"


def test_no_show_transitions_scheduled_directly(client, calendar_tenancy, db_session):
    from app.models.appointment import Appointment

    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=1000).json()
    row = db_session.get(Appointment, uuid.UUID(created["id"]))
    row.starts_at = datetime.now(UTC) - timedelta(minutes=5)
    db_session.flush()

    response = client.post(
        f"{APPOINTMENTS_URL}/{created['id']}/no-show",
        json={"expected_version": created["version"]},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "no_show"


def test_invalid_transition_from_cancelled_is_rejected(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=500).json()
    cancelled = client.post(
        f"{APPOINTMENTS_URL}/{created['id']}/cancel",
        json={"expected_version": created["version"], "reason": "test"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    ).json()

    response = client.post(
        f"{APPOINTMENTS_URL}/{created['id']}/confirm",
        json={"expected_version": cancelled["version"]},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_status_transition"


def test_inactive_provider_membership_is_rejected(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = _create(
        client, calendar_tenancy, tenancy.owner_a, provider_user_id=tenancy.inactive_member_a
    )
    assert response.status_code == 404


def test_cross_tenant_appointment_access_is_not_found(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=600).json()

    response = client.get(
        f"{APPOINTMENTS_URL}/{created['id']}",
        headers=dev_headers(tenancy.owner_b, tenancy.tenant_b.id),
    )
    assert response.status_code == 404


def test_unauthenticated_create_is_rejected(client, calendar_tenancy):
    response = client.post(APPOINTMENTS_URL, json=_create_payload(calendar_tenancy))
    assert response.status_code == 401


def test_override_availability_requires_reason_and_config_role(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    starts_at, ends_at = _near_future_window(minutes_ahead=700, duration_minutes=45)
    payload = {
        "provider_user_id": str(tenancy.owner_a),
        "room_id": None,
        "service_type_id": str(calendar_tenancy.service_type_a.id),
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "patient_display_name": "Override patient",
        "override_availability": True,
    }
    missing_reason = client.post(
        APPOINTMENTS_URL, json=payload, headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id)
    )
    assert missing_reason.status_code == 409

    operator_attempt = dict(payload)
    operator_attempt["override_reason"] = "Emergency"
    forbidden = client.post(
        APPOINTMENTS_URL,
        json=operator_attempt,
        headers=dev_headers(tenancy.operator_a, tenancy.tenant_a.id),
    )
    assert forbidden.status_code == 403

    owner_attempt = dict(payload)
    owner_attempt["override_reason"] = "Emergency"
    allowed = client.post(
        APPOINTMENTS_URL,
        json=owner_attempt,
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert allowed.status_code == 200


# --- update_metadata authorization (no self-scoped bypass) ----------------


def test_owner_can_update_metadata(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=1100).json()
    response = client.patch(
        f"{APPOINTMENTS_URL}/{created['id']}",
        json={"expected_version": created["version"], "notes": "Updated by owner"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    assert response.json()["notes"] == "Updated by owner"


def test_operator_can_update_metadata(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=1110).json()
    response = client.patch(
        f"{APPOINTMENTS_URL}/{created['id']}",
        json={"expected_version": created["version"], "notes": "Updated by operator"},
        headers=dev_headers(tenancy.operator_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200


def test_appointment_own_provider_without_write_role_cannot_update_metadata(
    client, calendar_tenancy, db_session
):
    # Regression for the Codex finding: being the appointment's own
    # provider must NOT bypass CALENDAR_WRITE_ROLES for metadata updates -
    # unlike complete/no-show, task.md grants no self-scoped exception
    # here. auditor_a is not in CALENDAR_WRITE_ROLES; make them the
    # appointment's provider directly via the DB (auditor_a has no
    # ProviderSchedule of their own, so create through owner_a and then
    # reassign provider_user_id for this specific authorization check).
    from app.models.appointment import Appointment

    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=1120).json()
    row = db_session.get(Appointment, uuid.UUID(created["id"]))
    row.provider_user_id = tenancy.auditor_a
    db_session.flush()

    response = client.patch(
        f"{APPOINTMENTS_URL}/{created['id']}",
        json={"expected_version": created["version"], "notes": "Should be rejected"},
        headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 403


def test_content_editor_cannot_update_metadata(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=1130).json()
    response = client.patch(
        f"{APPOINTMENTS_URL}/{created['id']}",
        json={"expected_version": created["version"], "notes": "Should be rejected"},
        headers=dev_headers(tenancy.content_editor_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 403


def test_auditor_cannot_update_metadata(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=1140).json()
    response = client.patch(
        f"{APPOINTMENTS_URL}/{created['id']}",
        json={"expected_version": created["version"], "notes": "Should be rejected"},
        headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 403


def test_metadata_update_stale_version_is_rejected(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=1150).json()
    response = client.patch(
        f"{APPOINTMENTS_URL}/{created['id']}",
        json={"expected_version": created["version"] + 1, "notes": "Stale"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "stale_version"


def test_metadata_update_schema_rejects_lifecycle_fields(client, calendar_tenancy):
    # AppointmentMetadataUpdate uses extra="forbid" - status/starts_at/
    # room_id/etc. are not accepted fields at all (they only ever change
    # through the explicit action/reschedule endpoints).
    tenancy = calendar_tenancy.tenancy
    created = _create(client, calendar_tenancy, tenancy.owner_a, minutes_ahead=1160).json()
    response = client.patch(
        f"{APPOINTMENTS_URL}/{created['id']}",
        json={"expected_version": created["version"], "status": "cancelled"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 422
