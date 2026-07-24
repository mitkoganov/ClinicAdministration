import pytest

from tests.integration.conftest import dev_headers

pytestmark = pytest.mark.integration

SERVICE_TYPES_URL = "/api/v1/appointment-service-types"


def test_owner_can_create_service_type(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.post(
        SERVICE_TYPES_URL,
        json={
            "name": "Consultation",
            "code": "CONS",
            "description": "Initial consultation",
            "default_duration_minutes": 45,
            "buffer_before_minutes": 5,
            "buffer_after_minutes": 5,
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["default_duration_minutes"] == 45
    assert body["buffer_before_minutes"] == 5


def test_operator_cannot_create_service_type(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.post(
        SERVICE_TYPES_URL,
        json={
            "name": "Consultation",
            "code": "CONS",
            "description": None,
            "default_duration_minutes": 45,
        },
        headers=dev_headers(tenancy.operator_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 403


def test_out_of_range_duration_is_rejected(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.post(
        SERVICE_TYPES_URL,
        json={
            "name": "Too long",
            "code": "TL",
            "description": None,
            "default_duration_minutes": 5000,
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 422


def test_duplicate_code_rejected(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.post(
        SERVICE_TYPES_URL,
        json={
            "name": "Duplicate",
            "code": calendar_tenancy.service_type_a.code,
            "description": None,
            "default_duration_minutes": 30,
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 409


def test_deactivate_service_type(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.post(
        f"{SERVICE_TYPES_URL}/{calendar_tenancy.service_type_a.id}/deactivate",
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "inactive"


def test_list_is_scoped_to_tenant(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    response = client.get(
        SERVICE_TYPES_URL, headers=dev_headers(tenancy.owner_b, tenancy.tenant_b.id)
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
