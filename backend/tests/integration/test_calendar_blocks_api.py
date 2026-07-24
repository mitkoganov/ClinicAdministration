from datetime import UTC, datetime, timedelta

import pytest

from tests.integration.conftest import dev_headers

pytestmark = pytest.mark.integration

BLOCKS_URL = "/api/v1/calendar-blocks"


def _window(hours_ahead=1000, duration_hours=2):
    starts_at = datetime.now(UTC) + timedelta(hours=hours_ahead)
    return starts_at, starts_at + timedelta(hours=duration_hours)


def test_owner_can_create_provider_block(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    starts_at, ends_at = _window()
    response = client.post(
        BLOCKS_URL,
        json={
            "provider_user_id": str(tenancy.owner_a),
            "room_id": None,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "reason": "Training session",
            "block_type": "training",
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    assert response.json()["block_type"] == "training"


def test_block_requires_provider_or_room(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    starts_at, ends_at = _window(hours_ahead=1010)
    response = client.post(
        BLOCKS_URL,
        json={
            "provider_user_id": None,
            "room_id": None,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "reason": "Nothing",
            "block_type": "other",
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 422


def test_appointment_creation_is_blocked_during_calendar_block(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    starts_at, ends_at = _window(hours_ahead=1020, duration_hours=1)
    block_response = client.post(
        BLOCKS_URL,
        json={
            "provider_user_id": str(tenancy.owner_a),
            "room_id": None,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "reason": "Leave",
            "block_type": "leave",
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert block_response.status_code == 200

    appointment_response = client.post(
        "/api/v1/appointments",
        json={
            "provider_user_id": str(tenancy.owner_a),
            "room_id": None,
            "service_type_id": str(calendar_tenancy.service_type_a.id),
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(minutes=30)).isoformat(),
            "patient_display_name": "Blocked patient",
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert appointment_response.status_code == 409
    assert appointment_response.json()["code"] == "blocked_period"


def test_appointment_creation_is_blocked_by_room_scoped_block(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    starts_at, ends_at = _window(hours_ahead=1060, duration_hours=1)
    block_response = client.post(
        BLOCKS_URL,
        json={
            "provider_user_id": None,
            "room_id": str(calendar_tenancy.room_a.id),
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "reason": "Room closed for cleaning",
            "block_type": "room_closure",
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert block_response.status_code == 200
    assert block_response.json()["block_type"] == "room_closure"

    appointment_response = client.post(
        "/api/v1/appointments",
        json={
            "provider_user_id": str(tenancy.owner_a),
            "room_id": str(calendar_tenancy.room_a.id),
            "service_type_id": str(calendar_tenancy.service_type_a.id),
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(minutes=30)).isoformat(),
            "patient_display_name": "Room-blocked patient",
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert appointment_response.status_code == 409
    assert appointment_response.json()["code"] == "room_unavailable"


def test_operator_cannot_delete_block(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    starts_at, ends_at = _window(hours_ahead=1030)
    created = client.post(
        BLOCKS_URL,
        json={
            "provider_user_id": str(tenancy.owner_a),
            "room_id": None,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "reason": "Maintenance",
            "block_type": "maintenance",
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    ).json()

    response = client.delete(
        f"{BLOCKS_URL}/{created['id']}",
        headers=dev_headers(tenancy.operator_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 403


def test_owner_can_delete_block(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    starts_at, ends_at = _window(hours_ahead=1040)
    created = client.post(
        BLOCKS_URL,
        json={
            "provider_user_id": str(tenancy.owner_a),
            "room_id": None,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "reason": "Personal",
            "block_type": "personal",
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    ).json()

    response = client.delete(
        f"{BLOCKS_URL}/{created['id']}", headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id)
    )
    assert response.status_code == 204


def test_list_in_range_is_scoped_to_tenant(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    starts_at, ends_at = _window(hours_ahead=1050)
    client.post(
        BLOCKS_URL,
        json={
            "provider_user_id": str(tenancy.owner_a),
            "room_id": None,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "reason": "Owner block",
            "block_type": "other",
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    response = client.get(
        BLOCKS_URL,
        params={
            "date_from": (starts_at - timedelta(days=1)).isoformat(),
            "date_to": (ends_at + timedelta(days=1)).isoformat(),
        },
        headers=dev_headers(tenancy.owner_b, tenancy.tenant_b.id),
    )
    assert response.status_code == 200
    assert response.json() == []
