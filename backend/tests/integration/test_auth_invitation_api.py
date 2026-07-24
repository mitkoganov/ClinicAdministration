import pytest

from app.core.config import Settings
from app.core.session_dependency import SESSION_COOKIE_NAME
from tests.integration.auth_api_helpers import (
    CSRF_COOKIE_NAME,
    LOGOUT_URL,
    ME_URL,
    SELECT_CLINIC_URL,
)
from tests.integration.auth_api_helpers import cookie_clear_header as _cookie_clear_header
from tests.integration.auth_api_helpers import csrf_headers as _csrf_headers
from tests.integration.auth_api_helpers import login as _login
from tests.integration.auth_api_helpers import (
    override_generous_rate_limiter as _override_generous_rate_limiter,
)

pytestmark = pytest.mark.integration


def test_invitation_accept_creates_session_for_the_invited_tenant_and_role(
    client, auth_tenancy, db_session
):
    import uuid

    from app.models.membership import MembershipRole
    from app.services.invitation_service import InvitationService

    settings = Settings(environment="development", session_cookie_secure=False)
    raw_token = InvitationService(db_session, settings).create_invitation(
        auth_tenancy.tenant_b.id,
        MembershipRole.OPERATOR,
        f"invited-{uuid.uuid4()}@auth.test",
        auth_tenancy.owner_user.id,
    )

    response = client.post(
        "/api/v1/auth/invitations/accept",
        json={
            "token": raw_token,
            "display_name": "Invited Person",
            "password": "a brand new invitee passphrase!",
        },
    )

    assert response.status_code == 200
    assert SESSION_COOKIE_NAME in client.cookies

    me_response = client.get(ME_URL)
    client.post(
        SELECT_CLINIC_URL,
        json={"tenant_id": str(auth_tenancy.tenant_b.id)},
        headers=_csrf_headers(client),
    )
    assert client.get(ME_URL).json()["role"] == "operator"
    assert me_response.status_code == 200


def test_invitation_accept_rejects_an_existing_inactive_account(client, auth_tenancy, db_session):
    """MED-004 repair (finding 1): an invitation must never be a side
    channel for reactivating a disabled account or issuing it a session -
    accepting an invitation whose invitee_email matches an existing
    inactive UserAccount must fail exactly like an invalid/expired token,
    with no session cookie set."""
    from app.models.membership import MembershipRole
    from app.services.invitation_service import InvitationService

    settings = Settings(environment="development", session_cookie_secure=False)
    raw_token = InvitationService(db_session, settings).create_invitation(
        auth_tenancy.tenant_b.id,
        MembershipRole.OPERATOR,
        auth_tenancy.inactive_account_user.normalized_email,
        auth_tenancy.owner_user.id,
    )

    response = client.post(
        "/api/v1/auth/invitations/accept",
        json={
            "token": raw_token,
            "display_name": "Should Not Be Reactivated",
            "password": "a brand new invitee passphrase!",
        },
    )

    assert response.status_code == 400
    assert SESSION_COOKIE_NAME not in client.cookies
    assert "csrf_token" not in client.cookies
    # The response must be indistinguishable from any other rejected
    # invitation token - it must not mention accounts or status at all.
    assert "inactive" not in response.json()["detail"].lower()
    assert "account" not in response.json()["detail"].lower()


def test_invitation_accept_cannot_take_over_an_existing_active_account(
    client, app, auth_tenancy, db_session
):
    """MED-004 repair (account-takeover finding): accepting an invitation
    whose invitee_email matches an existing ACTIVE account must never
    change that account's password or issue it a session, regardless of
    who obtained the invitation link. This is the core end-to-end
    regression test for the account-takeover vulnerability."""
    from app.models.membership import MembershipRole
    from app.services.invitation_service import InvitationService

    _override_generous_rate_limiter(app)
    settings = Settings(environment="development", session_cookie_secure=False)
    raw_token = InvitationService(db_session, settings).create_invitation(
        auth_tenancy.tenant_b.id,
        MembershipRole.OPERATOR,
        auth_tenancy.owner_user.normalized_email,
        auth_tenancy.owner_user.id,
    )

    response = client.post(
        "/api/v1/auth/invitations/accept",
        json={
            "token": raw_token,
            "display_name": "Attacker Chosen Name",
            "password": "attacker chosen new password!!",
        },
    )

    assert response.status_code == 400
    assert SESSION_COOKIE_NAME not in client.cookies
    assert "csrf_token" not in client.cookies
    assert "account" not in response.json()["detail"].lower()

    # The account's real password must still work, and the attacker's
    # chosen password must not.
    login_with_old_password = _login(
        client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password
    )
    assert login_with_old_password.status_code == 200
    client.post(LOGOUT_URL, headers=_csrf_headers(client))

    login_with_attacker_password = _login(
        client, auth_tenancy.owner_user.normalized_email, "attacker chosen new password!!"
    )
    assert login_with_attacker_password.status_code == 401


def test_invitation_accept_rejects_extra_fields_attempting_to_override_tenant_or_role(client):
    response = client.post(
        "/api/v1/auth/invitations/accept",
        json={
            "token": "irrelevant-since-schema-validation-runs-first",
            "display_name": "Attacker",
            "password": "a brand new invitee passphrase!",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "role": "owner",
        },
    )
    assert response.status_code == 422


def test_invalid_invitation_token_does_not_clear_unrelated_auth_cookies(client):
    """An anonymous invitation-acceptance failure has nothing to do with
    the caller's own session state (there usually isn't one) - it must
    never trigger the session-cookie-clearing behavior reserved for a
    stale session cookie."""
    response = client.post(
        "/api/v1/auth/invitations/accept",
        json={
            "token": "this-invitation-token-was-never-issued",
            "display_name": "Someone",
            "password": "a brand new invitee passphrase!",
        },
    )

    assert response.status_code == 400
    assert _cookie_clear_header(response, SESSION_COOKIE_NAME) is None
    assert _cookie_clear_header(response, CSRF_COOKIE_NAME) is None
