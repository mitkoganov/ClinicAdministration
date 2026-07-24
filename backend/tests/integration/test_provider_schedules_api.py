import pytest

from tests.integration.conftest import dev_headers

pytestmark = pytest.mark.integration

SCHEDULES_URL = "/api/v1/provider-schedules"


def test_manager_can_create_schedule_with_breaks(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.post(
        SCHEDULES_URL,
        json={
            "provider_user_id": str(tenancy.manager_a),
            "day_of_week": 0,
            "start_time": "09:00:00",
            "end_time": "17:00:00",
            "effective_from": "2020-01-01",
            "effective_until": None,
            "room_id": str(calendar_tenancy.room_a.id),
            "breaks": [{"start_time": "12:00:00", "end_time": "13:00:00", "label": "Lunch"}],
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["breaks"]) == 1
    assert body["breaks"][0]["label"] == "Lunch"


def test_overlapping_schedule_rule_for_same_provider_is_rejected(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    # owner_a already has a wide-open (00:00-23:59) rule for every weekday
    # from the calendar_tenancy fixture - any new rule for the same
    # provider/day/date-range necessarily overlaps it.
    response = client.post(
        SCHEDULES_URL,
        json={
            "provider_user_id": str(tenancy.owner_a),
            "day_of_week": 0,
            "start_time": "09:00:00",
            "end_time": "10:00:00",
            "effective_from": "2020-01-01",
            "effective_until": None,
            "room_id": None,
            "breaks": [],
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 409


def test_break_outside_schedule_window_is_rejected(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.post(
        SCHEDULES_URL,
        json={
            "provider_user_id": str(tenancy.manager_a),
            "day_of_week": 1,
            "start_time": "09:00:00",
            "end_time": "17:00:00",
            "effective_from": "2020-01-01",
            "effective_until": None,
            "room_id": None,
            "breaks": [{"start_time": "18:00:00", "end_time": "19:00:00", "label": None}],
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 409


def test_operator_cannot_create_schedule(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.post(
        SCHEDULES_URL,
        json={
            "provider_user_id": str(tenancy.manager_a),
            "day_of_week": 2,
            "start_time": "09:00:00",
            "end_time": "17:00:00",
            "effective_from": "2020-01-01",
            "effective_until": None,
            "room_id": None,
            "breaks": [],
        },
        headers=dev_headers(tenancy.operator_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 403


def test_auditor_can_read_schedules(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.get(
        SCHEDULES_URL, headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id)
    )
    assert response.status_code == 200
    assert response.json()["total"] >= 1


def test_deactivate_schedule(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    schedule_id = calendar_tenancy.schedules_a[0].id
    response = client.post(
        f"{SCHEDULES_URL}/{schedule_id}/deactivate",
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "inactive"


def test_cross_tenant_schedule_is_not_found(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    schedule_id = calendar_tenancy.schedules_a[0].id
    response = client.get(
        f"{SCHEDULES_URL}/{schedule_id}", headers=dev_headers(tenancy.owner_b, tenancy.tenant_b.id)
    )
    assert response.status_code == 404
