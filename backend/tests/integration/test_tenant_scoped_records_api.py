import logging
import uuid

import pytest

from tests.integration.conftest import dev_headers

pytestmark = pytest.mark.integration

RESOURCES_URL = "/api/v1/tenant-resources"


def test_tenant_a_lists_only_tenant_a_resources(client, tenancy):
    response = client.get(RESOURCES_URL, headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id))

    assert response.status_code == 200
    ids = {item["id"] for item in response.json()}
    assert ids == {str(tenancy.record_a.id)}


def test_tenant_b_lists_only_tenant_b_resources(client, tenancy):
    response = client.get(RESOURCES_URL, headers=dev_headers(tenancy.owner_b, tenancy.tenant_b.id))

    assert response.status_code == 200
    ids = {item["id"] for item in response.json()}
    assert ids == {str(tenancy.record_b.id)}


def test_tenant_a_cannot_read_tenant_b_resource(client, tenancy):
    response = client.get(
        f"{RESOURCES_URL}/{tenancy.record_b.id}",
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 404


def test_tenant_a_cannot_update_tenant_b_resource(client, tenancy):
    response = client.put(
        f"{RESOURCES_URL}/{tenancy.record_b.id}",
        json={"name": "Hijacked"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 404


def test_tenant_a_cannot_delete_tenant_b_resource(client, tenancy):
    response = client.delete(
        f"{RESOURCES_URL}/{tenancy.record_b.id}",
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 404


def test_create_always_derives_ownership_from_context(client, tenancy):
    response = client.post(
        RESOURCES_URL,
        json={"name": "Fresh record"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 201
    listing = client.get(
        RESOURCES_URL, headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id)
    ).json()
    assert any(item["name"] == "Fresh record" for item in listing)


def test_payload_tenant_id_override_is_ignored(client, tenancy):
    response = client.post(
        RESOURCES_URL,
        json={"name": "Sneaky record", "tenant_id": str(tenancy.tenant_b.id)},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 201
    # It must land in Tenant A, never Tenant B, regardless of the payload field.
    b_listing = client.get(
        RESOURCES_URL, headers=dev_headers(tenancy.owner_b, tenancy.tenant_b.id)
    ).json()
    assert not any(item["name"] == "Sneaky record" for item in b_listing)
    a_listing = client.get(
        RESOURCES_URL, headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id)
    ).json()
    assert any(item["name"] == "Sneaky record" for item in a_listing)


def test_auditor_may_read(client, tenancy):
    response = client.get(
        f"{RESOURCES_URL}/{tenancy.record_a.id}",
        headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 200


def test_auditor_cannot_create(client, tenancy):
    response = client.post(
        RESOURCES_URL,
        json={"name": "Nope"},
        headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 403


def test_auditor_cannot_update(client, tenancy):
    response = client.put(
        f"{RESOURCES_URL}/{tenancy.record_a.id}",
        json={"name": "Nope"},
        headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 403


def test_auditor_cannot_delete(client, tenancy):
    response = client.delete(
        f"{RESOURCES_URL}/{tenancy.record_a.id}",
        headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 403


def test_operator_may_create_and_update(client, tenancy):
    create_response = client.post(
        RESOURCES_URL,
        json={"name": "Operator record"},
        headers=dev_headers(tenancy.operator_a, tenancy.tenant_a.id),
    )
    assert create_response.status_code == 201
    record_id = create_response.json()["id"]

    update_response = client.put(
        f"{RESOURCES_URL}/{record_id}",
        json={"name": "Operator record updated"},
        headers=dev_headers(tenancy.operator_a, tenancy.tenant_a.id),
    )
    assert update_response.status_code == 200


def test_operator_cannot_delete(client, tenancy):
    response = client.delete(
        f"{RESOURCES_URL}/{tenancy.record_a.id}",
        headers=dev_headers(tenancy.operator_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 403


def test_manager_can_delete(client, tenancy):
    response = client.delete(
        f"{RESOURCES_URL}/{tenancy.record_a.id}",
        headers=dev_headers(tenancy.manager_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 204


def test_owner_can_delete(client, tenancy):
    response = client.delete(
        f"{RESOURCES_URL}/{tenancy.record_a.id}",
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 204


def test_error_responses_do_not_distinguish_missing_from_foreign_tenant(client, tenancy):
    foreign_response = client.get(
        f"{RESOURCES_URL}/{tenancy.record_b.id}",
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    missing_response = client.get(
        f"{RESOURCES_URL}/{uuid.uuid4()}",
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    assert foreign_response.status_code == missing_response.status_code == 404
    assert foreign_response.json() == missing_response.json()


def test_audit_event_emitted_for_successful_create(client, tenancy, caplog):
    with caplog.at_level(logging.INFO, logger="audit"):
        response = client.post(
            RESOURCES_URL,
            json={"name": "Audited create"},
            headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
        )

    assert response.status_code == 201
    events = [r.audit_event for r in caplog.records if hasattr(r, "audit_event")]
    assert any(
        e["event_type"] == "tenant_scoped_record.create" and e["outcome"] == "success"
        for e in events
    )


def test_audit_event_emitted_for_successful_update(client, tenancy, caplog):
    with caplog.at_level(logging.INFO, logger="audit"):
        response = client.put(
            f"{RESOURCES_URL}/{tenancy.record_a.id}",
            json={"name": "Audited update"},
            headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
        )

    assert response.status_code == 200
    events = [r.audit_event for r in caplog.records if hasattr(r, "audit_event")]
    assert any(
        e["event_type"] == "tenant_scoped_record.update" and e["outcome"] == "success"
        for e in events
    )


def test_audit_event_emitted_for_successful_delete(client, tenancy, caplog):
    with caplog.at_level(logging.INFO, logger="audit"):
        response = client.delete(
            f"{RESOURCES_URL}/{tenancy.record_a.id}",
            headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
        )

    assert response.status_code == 204
    events = [r.audit_event for r in caplog.records if hasattr(r, "audit_event")]
    assert any(
        e["event_type"] == "tenant_scoped_record.delete" and e["outcome"] == "success"
        for e in events
    )


def test_insufficient_role_mutation_emits_rejected_audit_event(client, tenancy, caplog):
    with caplog.at_level(logging.INFO, logger="audit"):
        response = client.post(
            RESOURCES_URL,
            json={"name": "Should be rejected"},
            headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id),
        )

    assert response.status_code == 403
    events = [r.audit_event for r in caplog.records if hasattr(r, "audit_event")]
    assert any(
        e["event_type"] == "tenant_scoped_record.create" and e["outcome"] == "rejected"
        for e in events
    )
