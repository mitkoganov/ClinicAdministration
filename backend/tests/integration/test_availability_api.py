from datetime import UTC, datetime, timedelta

import pytest

from tests.integration.conftest import dev_headers

pytestmark = pytest.mark.integration

AVAILABILITY_URL = "/api/v1/availability"


def test_availability_returns_slots_for_wide_open_schedule(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    today = datetime.now(UTC).date()
    response = client.get(
        AVAILABILITY_URL,
        params={
            "provider_id": str(tenancy.owner_a),
            "service_type_id": str(calendar_tenancy.service_type_a.id),
            "date_from": today.isoformat(),
            "date_to": today.isoformat(),
            "room_id": str(calendar_tenancy.room_a.id),
        },
        headers=dev_headers(tenancy.auditor_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_timezone"]
    assert len(body["slots"]) > 0


def test_availability_excludes_range_exceeding_max_days(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    today = datetime.now(UTC).date()
    response = client.get(
        AVAILABILITY_URL,
        params={
            "provider_id": str(tenancy.owner_a),
            "service_type_id": str(calendar_tenancy.service_type_a.id),
            "date_from": today.isoformat(),
            "date_to": (today + timedelta(days=40)).isoformat(),
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 409


def test_availability_rejects_content_editor_for_another_provider(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    today = datetime.now(UTC).date()
    response = client.get(
        AVAILABILITY_URL,
        params={
            "provider_id": str(tenancy.owner_a),
            "service_type_id": str(calendar_tenancy.service_type_a.id),
            "date_from": today.isoformat(),
            "date_to": today.isoformat(),
        },
        headers=dev_headers(tenancy.content_editor_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 403


def test_availability_self_scope_allowed_for_any_active_member(client, calendar_tenancy):
    # content_editor has no CALENDAR_READ_ROLES permission at all, but
    # task.md's authorization matrix grants every active member
    # self-scoped access to their OWN availability regardless of role -
    # "provider" is a fact (provider_user_id == caller), not a permission
    # grant. content_editor_a has no ProviderSchedule row in this fixture,
    # so the response is legitimately empty, but the request itself must
    # be authorized (200), not rejected (403).
    tenancy = calendar_tenancy.tenancy
    today = datetime.now(UTC).date()
    response = client.get(
        AVAILABILITY_URL,
        params={
            "provider_id": str(tenancy.content_editor_a),
            "service_type_id": str(calendar_tenancy.service_type_a.id),
            "date_from": today.isoformat(),
            "date_to": today.isoformat(),
        },
        headers=dev_headers(tenancy.content_editor_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    assert response.json()["slots"] == []


def test_availability_self_scope_does_not_allow_spoofing_another_provider(client, calendar_tenancy):
    # A non-privileged caller cannot use the self-scope rule to view
    # another provider's availability by simply passing a different
    # provider_id - the comparison is always against the SERVER-resolved
    # caller identity (context.user_id), never a client-supplied claim.
    tenancy = calendar_tenancy.tenancy
    today = datetime.now(UTC).date()
    response = client.get(
        AVAILABILITY_URL,
        params={
            "provider_id": str(tenancy.owner_a),
            "service_type_id": str(calendar_tenancy.service_type_a.id),
            "date_from": today.isoformat(),
            "date_to": today.isoformat(),
        },
        headers=dev_headers(tenancy.content_editor_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 403


def test_availability_operator_can_still_query_other_providers(client, calendar_tenancy):
    # Existing CALENDAR_READ_ROLES behavior (operator is in that set) must
    # not regress now that the self-scope carve-out exists.
    tenancy = calendar_tenancy.tenancy
    today = datetime.now(UTC).date()
    response = client.get(
        AVAILABILITY_URL,
        params={
            "provider_id": str(tenancy.owner_a),
            "service_type_id": str(calendar_tenancy.service_type_a.id),
            "date_from": today.isoformat(),
            "date_to": today.isoformat(),
            "room_id": str(calendar_tenancy.room_a.id),
        },
        headers=dev_headers(tenancy.operator_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    assert len(response.json()["slots"]) > 0


def test_availability_excludes_booked_slot(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    starts_at = datetime.now(UTC) + timedelta(minutes=10)
    ends_at = starts_at + timedelta(minutes=30)
    created = client.post(
        "/api/v1/appointments",
        json={
            "provider_user_id": str(tenancy.owner_a),
            "room_id": str(calendar_tenancy.room_a.id),
            "service_type_id": str(calendar_tenancy.service_type_a.id),
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "patient_display_name": "Booked patient",
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert created.status_code == 200

    today = starts_at.date()
    response = client.get(
        AVAILABILITY_URL,
        params={
            "provider_id": str(tenancy.owner_a),
            "service_type_id": str(calendar_tenancy.service_type_a.id),
            "date_from": today.isoformat(),
            "date_to": today.isoformat(),
            "room_id": str(calendar_tenancy.room_a.id),
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 200
    for slot in response.json()["slots"]:
        slot_start = datetime.fromisoformat(slot["starts_at"])
        slot_end = datetime.fromisoformat(slot["ends_at"])
        assert slot_end <= starts_at or slot_start >= ends_at, (
            f"slot {slot} overlaps the booked appointment"
        )


def test_availability_inactive_service_type_returns_conflict(client, calendar_tenancy):
    tenancy = calendar_tenancy.tenancy
    client.post(
        f"/api/v1/appointment-service-types/{calendar_tenancy.service_type_a.id}/deactivate",
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    today = datetime.now(UTC).date()
    response = client.get(
        AVAILABILITY_URL,
        params={
            "provider_id": str(tenancy.owner_a),
            "service_type_id": str(calendar_tenancy.service_type_a.id),
            "date_from": today.isoformat(),
            "date_to": today.isoformat(),
        },
        headers=dev_headers(tenancy.owner_a, tenancy.tenant_a.id),
    )
    assert response.status_code == 409
