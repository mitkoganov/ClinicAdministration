import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.integration.conftest import dev_headers

pytestmark = pytest.mark.integration

TENANT_CONTEXT_URL = "/api/v1/tenant-context"


def test_active_member_accesses_own_tenant_context(client, tenancy):
    response = client.get(
        TENANT_CONTEXT_URL, headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == str(tenancy.tenant_a.id)
    assert body["tenant_name"] == tenancy.tenant_a.name
    assert body["role"] == "owner"
    assert body["membership_status"] == "active"


def test_user_without_membership_is_rejected(client, tenancy):
    response = client.get(
        TENANT_CONTEXT_URL, headers=dev_headers(tenancy.stranger, tenancy.tenant_a.id)
    )

    assert response.status_code == 404


def test_inactive_membership_is_rejected(client, tenancy):
    response = client.get(
        TENANT_CONTEXT_URL, headers=dev_headers(tenancy.inactive_member_a, tenancy.tenant_a.id)
    )

    assert response.status_code == 404


def test_inactive_tenant_is_rejected(client, tenancy):
    response = client.get(
        TENANT_CONTEXT_URL, headers=dev_headers(tenancy.owner_a, tenancy.inactive_tenant.id)
    )

    assert response.status_code == 404


def test_unknown_tenant_is_rejected(client, tenancy):
    response = client.get(TENANT_CONTEXT_URL, headers=dev_headers(tenancy.owner_a, uuid.uuid4()))

    assert response.status_code == 404


def test_all_tenant_context_failure_modes_are_externally_identical(client, tenancy):
    """Unknown tenant, inactive tenant, missing membership, and inactive
    membership must be indistinguishable to the caller: same status, same
    JSON body, same content-type - not just "all 404s" with different
    detail text that would still leak which case applied."""
    scenarios = {
        "unknown_tenant": dev_headers(tenancy.owner_a, uuid.uuid4()),
        "inactive_tenant": dev_headers(tenancy.owner_a, tenancy.inactive_tenant.id),
        "missing_membership": dev_headers(tenancy.stranger, tenancy.tenant_a.id),
        "inactive_membership": dev_headers(tenancy.inactive_member_a, tenancy.tenant_a.id),
    }

    responses = {
        name: client.get(TENANT_CONTEXT_URL, headers=headers) for name, headers in scenarios.items()
    }

    for name, response in responses.items():
        assert response.status_code == 404, f"{name} did not return 404"

    bodies = {name: response.json() for name, response in responses.items()}
    content_types = {name: response.headers["content-type"] for name, response in responses.items()}

    first_body = next(iter(bodies.values()))
    first_content_type = next(iter(content_types.values()))
    for name, body in bodies.items():
        assert body == first_body, f"{name} returned a distinguishable body: {body}"
    for name, content_type in content_types.items():
        assert content_type == first_content_type, f"{name} returned a distinguishable content-type"


def test_missing_tenant_header_is_rejected(client, tenancy):
    response = client.get(TENANT_CONTEXT_URL, headers={"X-Dev-User-Id": str(tenancy.owner_a)})

    assert response.status_code == 401


def test_missing_development_identity_is_rejected():
    """With development identity disabled (the production-safe default),
    every tenant-scoped route must reject the request regardless of which
    headers are supplied."""
    client = TestClient(create_app())

    response = client.get(TENANT_CONTEXT_URL, headers=dev_headers(uuid.uuid4(), uuid.uuid4()))

    assert response.status_code == 401


def test_response_does_not_expose_membership_internals(client, tenancy):
    response = client.get(
        TENANT_CONTEXT_URL, headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id)
    )

    assert "membership_id" not in response.json()


def test_health_endpoint_still_works(client):
    assert client.get("/health").status_code == 200


def test_ready_endpoint_still_works(client):
    assert client.get("/ready").status_code == 200
