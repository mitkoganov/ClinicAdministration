import logging
import uuid

import pytest

from tests.integration.conftest import dev_headers

pytestmark = pytest.mark.integration

STAFF_URL = "/api/v1/clinic/staff"


def _membership_id(client, tenancy, user_id, actor=None, tenant=None) -> str:
    tenant = tenant or tenancy.tenant_a
    actor = actor or tenancy.owner_a
    response = client.get(STAFF_URL, params={"limit": 100}, headers=dev_headers(actor, tenant.id))
    match = [item for item in response.json()["items"] if item["user_id"] == str(user_id)]
    assert match, f"no membership found for user {user_id} in tenant {tenant.id}"
    return match[0]["id"]


def test_owner_can_list_staff(client, tenancy):
    response = client.get(STAFF_URL, headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 5
    assert all("id" in item and "user_id" in item for item in body["items"])


def test_cross_tenant_staff_never_appears(client, tenancy):
    response = client.get(
        STAFF_URL, params={"limit": 100}, headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id)
    )

    ids = {item["user_id"] for item in response.json()["items"]}
    assert str(tenancy.owner_b) not in ids


def test_cross_tenant_membership_id_returns_identical_not_found(client, tenancy):
    other_tenant_membership_id = _membership_id(
        client, tenancy, tenancy.owner_b, actor=tenancy.owner_b, tenant=tenancy.tenant_b
    )

    foreign_response = client.patch(
        f"{STAFF_URL}/{other_tenant_membership_id}",
        json={"role": "manager"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    missing_response = client.patch(
        f"{STAFF_URL}/{uuid.uuid4()}",
        json={"role": "manager"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    assert foreign_response.status_code == missing_response.status_code == 404
    assert foreign_response.json() == missing_response.json()


def test_owner_can_add_allowed_membership(client, tenancy):
    new_user = str(uuid.uuid4())
    response = client.post(
        STAFF_URL,
        json={"user_id": new_user, "role": "operator"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 201
    assert response.json()["user_id"] == new_user
    assert response.json()["role"] == "operator"


def test_duplicate_membership_is_rejected(client, tenancy):
    response = client.post(
        STAFF_URL,
        json={"user_id": str(tenancy.manager_a), "role": "operator"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 409


def test_manager_cannot_grant_owner(client, tenancy):
    response = client.post(
        STAFF_URL,
        json={"user_id": str(uuid.uuid4()), "role": "owner"},
        headers=dev_headers(tenancy.manager_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 403


def test_manager_cannot_mutate_owner(client, tenancy):
    owner_membership_id = _membership_id(client, tenancy, tenancy.owner_a)

    response = client.patch(
        f"{STAFF_URL}/{owner_membership_id}",
        json={"role": "manager"},
        headers=dev_headers(tenancy.manager_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 403


def test_operator_cannot_manage_staff(client, tenancy):
    list_response = client.get(
        STAFF_URL, headers=dev_headers(tenancy.operator_a, tenancy.tenant_a.id)
    )
    assert list_response.status_code == 403

    create_response = client.post(
        STAFF_URL,
        json={"user_id": str(uuid.uuid4()), "role": "operator"},
        headers=dev_headers(tenancy.operator_a, tenancy.tenant_a.id),
    )
    assert create_response.status_code == 403


def test_auditor_cannot_manage_staff(client, tenancy):
    target_id = _membership_id(client, tenancy, tenancy.content_editor_a)

    update_response = client.patch(
        f"{STAFF_URL}/{target_id}",
        json={"status": "inactive"},
        headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id),
    )
    assert update_response.status_code == 403

    delete_response = client.delete(
        f"{STAFF_URL}/{target_id}",
        headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id),
    )
    assert delete_response.status_code == 403


def _make_owner_a_the_sole_active_owner(client, tenancy) -> None:
    # tenant_a's base fixture has two active owners (owner_a and
    # dual_member) - demote dual_member first so owner_a becomes the
    # clinic's one and only active owner for the final-owner tests below.
    dual_member_id = _membership_id(client, tenancy, tenancy.dual_member)
    response = client.patch(
        f"{STAFF_URL}/{dual_member_id}",
        json={"role": "manager"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200


def test_final_owner_cannot_be_demoted(client, tenancy):
    _make_owner_a_the_sole_active_owner(client, tenancy)
    owner_membership_id = _membership_id(client, tenancy, tenancy.owner_a)

    response = client.patch(
        f"{STAFF_URL}/{owner_membership_id}",
        json={"role": "manager"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 409


def test_final_owner_cannot_be_deactivated(client, tenancy):
    _make_owner_a_the_sole_active_owner(client, tenancy)
    owner_membership_id = _membership_id(client, tenancy, tenancy.owner_a)

    response = client.patch(
        f"{STAFF_URL}/{owner_membership_id}",
        json={"status": "inactive"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 409


def test_final_owner_cannot_be_removed(client, tenancy):
    _make_owner_a_the_sole_active_owner(client, tenancy)
    owner_membership_id = _membership_id(client, tenancy, tenancy.owner_a)

    response = client.delete(
        f"{STAFF_URL}/{owner_membership_id}",
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )

    assert response.status_code == 409


def test_role_change_is_audited_only_after_commit(client, tenancy, caplog):
    target_id = _membership_id(client, tenancy, tenancy.operator_a)

    with caplog.at_level(logging.INFO, logger="audit"):
        response = client.patch(
            f"{STAFF_URL}/{target_id}",
            json={"role": "auditor"},
            headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
        )

    assert response.status_code == 200
    events = [r.audit_event for r in caplog.records if hasattr(r, "audit_event")]
    assert any(
        e["event_type"] == "membership.role_changed" and e["outcome"] == "success" for e in events
    )


def test_failed_commit_does_not_emit_success_audit(client, tenancy, monkeypatch, caplog):
    from app.api import staff as staff_api

    original_create = staff_api.StaffService.create

    def _boom(self, *args, **kwargs):
        self._db.commit = lambda: (_ for _ in ()).throw(RuntimeError("simulated"))
        return original_create(self, *args, **kwargs)

    monkeypatch.setattr(staff_api.StaffService, "create", _boom)

    # TestClient re-raises unhandled exceptions by default (rather than
    # returning the 500 response our own handler would produce for a real
    # deployment) - the point of this test is only that no SUCCESS audit
    # event is emitted when the commit itself fails, which is already true
    # by the time the exception propagates here.
    with (
        caplog.at_level(logging.INFO, logger="audit"),
        pytest.raises(RuntimeError, match="simulated"),
    ):
        client.post(
            STAFF_URL,
            json={"user_id": str(uuid.uuid4()), "role": "operator"},
            headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
        )

    events = [r.audit_event for r in caplog.records if hasattr(r, "audit_event")]
    assert not any(
        e["event_type"] == "membership.create" and e["outcome"] == "success" for e in events
    )


def test_tenant_and_membership_deactivation_are_respected_immediately(client, tenancy):
    inactive_response = client.get(
        STAFF_URL,
        headers=dev_headers(tenancy.inactive_member_a, tenancy.tenant_a.id),
    )
    assert inactive_response.status_code == 404

    inactive_tenant_response = client.get(
        STAFF_URL,
        headers=dev_headers(tenancy.owner_a, tenancy.inactive_tenant.id),
    )
    assert inactive_tenant_response.status_code == 404


def test_pagination_and_filters_remain_tenant_scoped(client, tenancy):
    page1 = client.get(
        STAFF_URL,
        params={"limit": 2, "offset": 0},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    ).json()
    page2 = client.get(
        STAFF_URL,
        params={"limit": 2, "offset": 2},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    ).json()

    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 2
    assert {i["id"] for i in page1["items"]}.isdisjoint({i["id"] for i in page2["items"]})

    owner_filtered = client.get(
        STAFF_URL,
        params={"role": "owner"},
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    ).json()
    assert all(item["role"] == "owner" for item in owner_filtered["items"])
    assert str(tenancy.owner_b) not in {item["user_id"] for item in owner_filtered["items"]}
