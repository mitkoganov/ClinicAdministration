import pytest

from tests.integration.conftest import dev_headers

pytestmark = pytest.mark.integration

ROOMS_URL = "/api/v1/rooms"


def test_owner_can_create_room(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.post(
        ROOMS_URL,
        json={"name": "Room 2", "code": "R2", "description": "Second room"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Room 2"
    assert body["code"] == "R2"
    assert body["status"] == "active"


def test_operator_cannot_create_room(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.post(
        ROOMS_URL,
        json={"name": "Room 2", "code": "R2", "description": None},
        headers=dev_headers(tenancy.operator_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 403


def test_auditor_can_list_rooms_but_not_create(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    list_response = client.get(
        ROOMS_URL, headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id)
    )
    assert list_response.status_code == 200
    assert any(r["id"] == str(calendar_tenancy.room_a.id) for r in list_response.json()["items"])

    create_response = client.post(
        ROOMS_URL,
        json={"name": "X", "code": "X1", "description": None},
        headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id),
    )
    assert create_response.status_code == 403


def test_duplicate_code_in_same_tenant_is_rejected(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.post(
        ROOMS_URL,
        json={"name": "Dup", "code": calendar_tenancy.room_a.code, "description": None},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 409


def test_same_code_allowed_in_different_tenant(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.post(
        ROOMS_URL,
        json={
            "name": "Same code different tenant",
            "code": calendar_tenancy.room_a.code,
            "description": None,
        },
        headers=dev_headers(tenancy.owner_b, tenancy.tenant_b.id),
    )
    assert response.status_code == 200


def test_cross_tenant_room_returns_not_found(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.get(
        f"{ROOMS_URL}/{calendar_tenancy.room_a.id}",
        headers=dev_headers(tenancy.owner_b, tenancy.tenant_b.id),
    )
    assert response.status_code == 404


def test_deactivate_room_then_reject_new_appointment(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.post(
        f"{ROOMS_URL}/{calendar_tenancy.room_a.id}/deactivate",
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "inactive"


def test_update_room_name(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.patch(
        f"{ROOMS_URL}/{calendar_tenancy.room_a.id}",
        json={"name": "Renamed Room"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed Room"


def test_unauthenticated_request_is_rejected(client, calendar_tenancy):
    response = client.get(ROOMS_URL)
    assert response.status_code == 401
