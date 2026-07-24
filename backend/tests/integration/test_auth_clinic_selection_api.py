import pytest

from tests.integration.auth_api_helpers import CLINICS_URL, ME_URL, SELECT_CLINIC_URL
from tests.integration.auth_api_helpers import csrf_headers as _csrf_headers
from tests.integration.auth_api_helpers import login as _login
from tests.integration.auth_api_helpers import (
    override_generous_rate_limiter as _override_generous_rate_limiter,
)

pytestmark = pytest.mark.integration


def test_clinics_list_includes_only_active_memberships(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(
        client, auth_tenancy.dual_clinic_user.normalized_email, auth_tenancy.dual_clinic_password
    )

    response = client.get(CLINICS_URL)
    tenant_ids = {item["tenant_id"] for item in response.json()["items"]}
    assert tenant_ids == {str(auth_tenancy.tenant_a.id), str(auth_tenancy.tenant_b.id)}


def test_clinics_list_is_empty_for_a_user_with_no_membership(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(
        client,
        auth_tenancy.no_membership_user.normalized_email,
        auth_tenancy.no_membership_password,
    )

    response = client.get(CLINICS_URL)
    assert response.json()["items"] == []


def test_select_clinic_and_me_reflects_it(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    select_response = client.post(
        SELECT_CLINIC_URL,
        json={"tenant_id": str(auth_tenancy.tenant_a.id)},
        headers=_csrf_headers(client),
    )
    assert select_response.status_code == 200

    me_response = client.get(ME_URL)
    assert me_response.json()["selected_clinic"]["tenant_id"] == str(auth_tenancy.tenant_a.id)
    assert me_response.json()["role"] == "owner"


def test_cross_tenant_clinic_selection_is_rejected(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    response = client.post(
        SELECT_CLINIC_URL,
        json={"tenant_id": str(auth_tenancy.tenant_b.id)},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404


def test_role_is_reloaded_from_the_database_on_every_request(client, app, auth_tenancy, db_session):
    from app.models.membership import MembershipRole
    from app.repositories.membership import MembershipRepository

    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    client.post(
        SELECT_CLINIC_URL,
        json={"tenant_id": str(auth_tenancy.tenant_a.id)},
        headers=_csrf_headers(client),
    )
    assert client.get(ME_URL).json()["role"] == "owner"

    membership_repo = MembershipRepository(db_session)
    membership = membership_repo.get_membership(
        auth_tenancy.tenant_a.id, auth_tenancy.owner_user.id
    )
    membership_repo.update(auth_tenancy.tenant_a.id, membership.id, role=MembershipRole.MANAGER)
    db_session.flush()

    assert client.get(ME_URL).json()["role"] == "manager"


def test_inactive_membership_loses_access_immediately(client, app, auth_tenancy, db_session):
    from app.models.membership import MembershipStatus
    from app.repositories.membership import MembershipRepository

    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    client.post(
        SELECT_CLINIC_URL,
        json={"tenant_id": str(auth_tenancy.tenant_a.id)},
        headers=_csrf_headers(client),
    )
    assert client.get(ME_URL).json()["selected_clinic"] is not None

    membership_repo = MembershipRepository(db_session)
    membership = membership_repo.get_membership(
        auth_tenancy.tenant_a.id, auth_tenancy.owner_user.id
    )
    membership_repo.update(
        auth_tenancy.tenant_a.id, membership.id, status=MembershipStatus.INACTIVE
    )
    db_session.flush()

    assert client.get(ME_URL).json()["selected_clinic"] is None


def test_inactive_tenant_loses_access_immediately(client, app, auth_tenancy, db_session):
    from app.models.tenant import TenantStatus

    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    client.post(
        SELECT_CLINIC_URL,
        json={"tenant_id": str(auth_tenancy.tenant_a.id)},
        headers=_csrf_headers(client),
    )
    assert client.get(ME_URL).json()["selected_clinic"] is not None

    auth_tenancy.tenant_a.status = TenantStatus.INACTIVE
    db_session.flush()

    assert client.get(ME_URL).json()["selected_clinic"] is None
