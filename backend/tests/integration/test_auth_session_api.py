import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.session_dependency import SESSION_COOKIE_NAME
from tests.integration.auth_api_helpers import CSRF_COOKIE_NAME, ME_URL, SELECT_CLINIC_URL
from tests.integration.auth_api_helpers import assert_cookie_cleared as _assert_cookie_cleared
from tests.integration.auth_api_helpers import cookie_clear_header as _cookie_clear_header
from tests.integration.auth_api_helpers import csrf_headers as _csrf_headers
from tests.integration.auth_api_helpers import login as _login
from tests.integration.auth_api_helpers import (
    override_generous_rate_limiter as _override_generous_rate_limiter,
)

pytestmark = pytest.mark.integration


def test_me_returns_safe_identity_fields(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    response = client.get(ME_URL)
    body = response.json()
    assert body["user_id"] == str(auth_tenancy.owner_user.id)
    assert body["email"] == auth_tenancy.owner_user.normalized_email
    assert "password_hash" not in body
    assert "session_token" not in body


def test_me_never_reports_an_expiry_later_than_the_absolute_cap(
    client, app, auth_tenancy, db_session
):
    """MED-004 repair (finding 3): even a legacy/inconsistent row where
    idle_expires_at is later than absolute_expires_at must never leak
    outward - GET /auth/me always reports the effective (earlier) one."""
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    absolute_cap = datetime.now(UTC) + timedelta(minutes=5)
    db_session.execute(
        text(
            "UPDATE auth_sessions SET absolute_expires_at = :absolute_cap, "
            "idle_expires_at = :idle_expires_at WHERE user_id = :user_id"
        ),
        {
            "absolute_cap": absolute_cap,
            "idle_expires_at": absolute_cap + timedelta(hours=1),
            "user_id": str(auth_tenancy.owner_user.id),
        },
    )
    db_session.flush()

    response = client.get(ME_URL)
    assert response.status_code == 200
    reported_expiry = datetime.fromisoformat(response.json()["session_expires_at"])
    assert reported_expiry <= absolute_cap


def test_me_without_a_session_is_unauthorized(client):
    response = client.get(ME_URL)
    assert response.status_code == 401
    # No session cookie was ever sent - there is nothing stale to clear,
    # and this must not be treated as if there were.
    assert _cookie_clear_header(response, SESSION_COOKIE_NAME) is None
    assert _cookie_clear_header(response, CSRF_COOKIE_NAME) is None


def test_valid_session_request_does_not_clear_cookies(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    response = client.get(ME_URL)

    assert response.status_code == 200
    assert _cookie_clear_header(response, SESSION_COOKIE_NAME) is None
    assert _cookie_clear_header(response, CSRF_COOKIE_NAME) is None


def test_absolute_expired_session_is_rejected(client, app, auth_tenancy, db_session):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    db_session.execute(
        text("UPDATE auth_sessions SET absolute_expires_at = :past WHERE user_id = :user_id"),
        {
            "past": datetime.now(UTC) - timedelta(seconds=1),
            "user_id": str(auth_tenancy.owner_user.id),
        },
    )
    db_session.flush()

    response = client.get(ME_URL)
    assert response.status_code == 401
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_idle_expired_session_is_rejected(client, app, auth_tenancy, db_session):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    db_session.execute(
        text("UPDATE auth_sessions SET idle_expires_at = :past WHERE user_id = :user_id"),
        {
            "past": datetime.now(UTC) - timedelta(seconds=1),
            "user_id": str(auth_tenancy.owner_user.id),
        },
    )
    db_session.flush()

    response = client.get(ME_URL)
    assert response.status_code == 401
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_unknown_session_token_clears_stale_cookies(client):
    client.cookies.set(SESSION_COOKIE_NAME, "this-token-was-never-issued")
    client.cookies.set(CSRF_COOKIE_NAME, "some-stale-csrf-value")

    response = client.get(ME_URL)

    assert response.status_code == 401
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_malformed_session_token_clears_stale_cookies(client):
    # Not a real token shape at all (session tokens are
    # secrets.token_urlsafe(32) output) - still just an unknown-token
    # lookup miss, same code path as any other unrecognized token, but
    # covered explicitly per task.md's required test list.
    client.cookies.set(SESSION_COOKIE_NAME, "%%%not-a-real-token-shape%%%")
    client.cookies.set(CSRF_COOKIE_NAME, "some-stale-csrf-value")

    response = client.get(ME_URL)

    assert response.status_code == 401
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_inactive_account_session_clears_stale_cookies(client, app, auth_tenancy, db_session):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    db_session.execute(
        text("UPDATE user_accounts SET status = 'inactive' WHERE id = :user_id"),
        {"user_id": str(auth_tenancy.owner_user.id)},
    )
    db_session.flush()
    # The raw UPDATE above bypasses the ORM - the already-loaded
    # UserAccount instance in this shared session's identity map still
    # has status=active in memory until it's expired and re-fetched.
    db_session.expire_all()

    response = client.get(ME_URL)

    assert response.status_code == 401
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_stale_session_response_never_logs_the_raw_token(client, caplog):
    client.cookies.set(SESSION_COOKIE_NAME, "this-token-was-never-issued")

    with caplog.at_level(logging.DEBUG):
        response = client.get(ME_URL)

    assert response.status_code == 401
    for record in caplog.records:
        assert "this-token-was-never-issued" not in record.getMessage()


def test_client_can_log_in_again_after_a_stale_session_response(
    client, app, auth_tenancy, db_session
):
    # Revoked server-side (rather than via logout + a manually re-injected
    # cookie) so the client's cookie jar only ever holds cookies the
    # server itself actually set - manually calling client.cookies.set()
    # for a name the server will set again later in the same test causes
    # httpx to see two conflicting entries (different implicit cookie
    # domains) for that name.
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    db_session.execute(
        text("UPDATE auth_sessions SET revoked_at = now() WHERE user_id = :user_id"),
        {"user_id": str(auth_tenancy.owner_user.id)},
    )
    db_session.flush()

    stale_response = client.get(ME_URL)
    assert stale_response.status_code == 401

    fresh_login = _login(
        client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password
    )
    assert fresh_login.status_code == 200
    assert client.get(ME_URL).status_code == 200


def test_forbidden_response_does_not_clear_a_valid_session_cookie(client, app, auth_tenancy):
    """A 403 (insufficient role) is not a session problem at all - the
    session itself is perfectly valid, so it must never be cleared."""
    _override_generous_rate_limiter(app)
    _login(
        client, auth_tenancy.dual_clinic_user.normalized_email, auth_tenancy.dual_clinic_password
    )
    client.post(
        SELECT_CLINIC_URL,
        json={"tenant_id": str(auth_tenancy.tenant_a.id)},
        headers=_csrf_headers(client),
    )

    # dual_clinic_user is only a MANAGER in tenant_a - clinic name updates
    # require OWNER.
    response = client.patch(
        "/api/v1/clinic", json={"name": "Hijacked Name"}, headers=_csrf_headers(client)
    )

    assert response.status_code == 403
    assert _cookie_clear_header(response, SESSION_COOKIE_NAME) is None
    assert _cookie_clear_header(response, CSRF_COOKIE_NAME) is None
    assert client.get(ME_URL).status_code == 200
