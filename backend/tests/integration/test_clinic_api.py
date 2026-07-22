import pytest

from tests.integration.conftest import dev_headers

pytestmark = pytest.mark.integration

CLINIC_URL = "/api/v1/clinic"


def test_owner_can_view_and_update_own_clinic(client, tenancy):
    get_response = client.get(CLINIC_URL, headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id))
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["id"] == str(tenancy.tenant_a.id)
    assert body["role"] == "owner"

    patch_response = client.patch(
        CLINIC_URL,
        json={"name": "Acme Clinic"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["name"] == "Acme Clinic"


def test_manager_can_view_but_not_update_clinic(client, tenancy):
    get_response = client.get(
        CLINIC_URL, headers=dev_headers(tenancy.manager_a, tenancy.tenant_a.id)
    )
    assert get_response.status_code == 200
    assert get_response.json()["role"] == "manager"

    patch_response = client.patch(
        CLINIC_URL,
        json={"name": "Hijacked"},
        headers=dev_headers(tenancy.manager_a, tenancy.tenant_a.id),
    )
    assert patch_response.status_code == 403


def test_operator_cannot_update_clinic_settings(client, tenancy):
    response = client.patch(
        CLINIC_URL,
        json={"name": "Hijacked"},
        headers=dev_headers(tenancy.operator_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 403


def test_auditor_is_read_only(client, tenancy):
    get_response = client.get(
        CLINIC_URL, headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id)
    )
    assert get_response.status_code == 200

    patch_response = client.patch(
        CLINIC_URL,
        json={"name": "Hijacked"},
        headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id),
    )
    assert patch_response.status_code == 403


def test_clinic_update_rejects_unknown_fields(client, tenancy):
    response = client.patch(
        CLINIC_URL,
        json={"name": "Acme Clinic", "status": "inactive"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 422


def test_clinic_update_never_affects_another_tenant(client, tenancy):
    client.patch(
        CLINIC_URL,
        json={"name": "Tenant A Renamed"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    tenant_b_response = client.get(
        CLINIC_URL, headers=dev_headers(tenancy.owner_b, tenancy.tenant_b.id)
    )
    assert tenant_b_response.json()["name"] == tenancy.tenant_b.name
