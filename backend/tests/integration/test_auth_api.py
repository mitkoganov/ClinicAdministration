import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimiter, get_login_rate_limiter
from app.core.session_dependency import SESSION_COOKIE_NAME
from tests.integration.conftest import dev_headers

pytestmark = pytest.mark.integration

LOGIN_URL = "/api/v1/auth/login"
LOGOUT_URL = "/api/v1/auth/logout"
ME_URL = "/api/v1/auth/me"
CLINICS_URL = "/api/v1/auth/clinics"
SELECT_CLINIC_URL = "/api/v1/auth/select-clinic"
CHANGE_PASSWORD_URL = "/api/v1/auth/change-password"
PASSWORD_RESET_REQUEST_URL = "/api/v1/auth/password-reset/request"
TENANT_CONTEXT_URL = "/api/v1/tenant-context"


class _FakeStore:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    def expire(self, key: str, seconds: int) -> None:
        pass

    def ttl(self, key: str) -> int:
        return -1

    def delete(self, key: str) -> None:
        self._counts.pop(key, None)


def _override_generous_rate_limiter(app) -> None:
    store = _FakeStore()
    app.dependency_overrides[get_login_rate_limiter] = lambda: RateLimiter(
        store, max_attempts=1000, window_seconds=900
    )


def _login(client, email: str, password: str):
    return client.post(LOGIN_URL, json={"email": email, "password": password})


def _csrf_headers(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("csrf_token")}


CSRF_COOKIE_NAME = "csrf_token"


def _set_cookie_headers(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def _cookie_clear_header(response, name: str) -> str | None:
    for header in _set_cookie_headers(response):
        if header.startswith(f"{name}="):
            return header
    return None


def _assert_cookie_cleared(response, name: str) -> None:
    header = _cookie_clear_header(response, name)
    assert header is not None, f"expected a Set-Cookie header clearing {name!r}"
    lowered = header.lower()
    assert "max-age=0" in lowered
    assert "path=/" in lowered
    # The cleared cookie's own value must never carry the raw token.
    value = header.split(";", 1)[0].split("=", 1)[1]
    assert value in ("", '""')


def test_valid_login_creates_session_and_secure_cookie(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    response = _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    assert response.status_code == 200
    assert SESSION_COOKIE_NAME in client.cookies
    assert "csrf_token" in client.cookies
    # The response body never contains the raw token under any key.
    assert "token" not in response.text.lower()


def test_invalid_password_returns_generic_failure(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    response = _login(client, auth_tenancy.owner_user.normalized_email, "the wrong passphrase!!")

    assert response.status_code == 401
    assert SESSION_COOKIE_NAME not in client.cookies
    # A failed login never carried a session cookie to begin with - it
    # must not emit any cookie-clearing Set-Cookie header either.
    assert _cookie_clear_header(response, SESSION_COOKIE_NAME) is None
    assert _cookie_clear_header(response, CSRF_COOKIE_NAME) is None


def test_nonexistent_account_has_the_same_response_shape(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    real = _login(client, auth_tenancy.owner_user.normalized_email, "the wrong passphrase!!")
    client.cookies.clear()
    fake = _login(client, "nobody-at-all@auth.test", "some random passphrase!!")

    assert real.status_code == fake.status_code == 401
    assert real.json() == fake.json()


def test_inactive_account_cannot_log_in(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    response = _login(
        client,
        auth_tenancy.inactive_account_user.normalized_email,
        auth_tenancy.inactive_account_password,
    )

    assert response.status_code == 401
    assert SESSION_COOKIE_NAME not in client.cookies


def test_password_hash_is_never_returned(client, app, auth_tenancy, db_session):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    me_response = client.get(ME_URL)
    assert me_response.status_code == 200
    assert auth_tenancy.owner_user.password_hash not in me_response.text
    assert "password" not in me_response.json()


def test_raw_session_token_is_never_persisted(client, app, auth_tenancy, db_session):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    raw_cookie_value = client.cookies.get(SESSION_COOKIE_NAME)

    persisted = (
        db_session.execute(text("SELECT session_token_hash FROM auth_sessions")).scalars().all()
    )
    assert raw_cookie_value not in persisted


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


def test_logout_revokes_session_and_clears_cookie(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    logout_response = client.post(LOGOUT_URL, headers=_csrf_headers(client))
    assert logout_response.status_code == 200
    assert not client.cookies.get(SESSION_COOKIE_NAME)

    me_after_logout = client.get(ME_URL)
    assert me_after_logout.status_code == 401


def test_logout_is_idempotent_without_a_session(client):
    response = client.post(LOGOUT_URL)
    assert response.status_code == 200


def test_revoked_session_cannot_be_reused(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    # Captured BEFORE logout: logout's own Set-Cookie (Max-Age=0) makes
    # httpx remove session_token from the jar immediately, so reading it
    # afterward would just be None.
    stale_cookie = client.cookies.get(SESSION_COOKIE_NAME)
    stale_csrf = client.cookies.get(CSRF_COOKIE_NAME)
    client.post(LOGOUT_URL, headers=_csrf_headers(client))

    client.cookies.set(SESSION_COOKIE_NAME, stale_cookie)
    client.cookies.set(CSRF_COOKIE_NAME, stale_csrf)
    response = client.get(ME_URL)

    assert response.status_code == 401
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


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


# --- MED-004 repair: get_current_session_optional must not swallow a
# stale (rather than simply missing) session cookie for tenant-scoped
# routes that combine session auth with the dev-identity fallback (see
# app.core.tenant_context.get_tenant_context). ------------------------


def _select_clinic(client, tenant_id) -> None:
    response = client.post(
        SELECT_CLINIC_URL, json={"tenant_id": str(tenant_id)}, headers=_csrf_headers(client)
    )
    assert response.status_code == 200


def test_tenant_route_expired_session_clears_stale_cookies(client, app, auth_tenancy, db_session):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    _select_clinic(client, auth_tenancy.tenant_a.id)
    db_session.execute(
        text("UPDATE auth_sessions SET absolute_expires_at = :past WHERE user_id = :user_id"),
        {
            "past": datetime.now(UTC) - timedelta(seconds=1),
            "user_id": str(auth_tenancy.owner_user.id),
        },
    )
    db_session.flush()

    response = client.get(TENANT_CONTEXT_URL)

    assert response.status_code == 401
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_tenant_route_revoked_session_clears_stale_cookies(client, app, auth_tenancy, db_session):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    _select_clinic(client, auth_tenancy.tenant_a.id)
    db_session.execute(
        text("UPDATE auth_sessions SET revoked_at = now() WHERE user_id = :user_id"),
        {"user_id": str(auth_tenancy.owner_user.id)},
    )
    db_session.flush()

    response = client.get(TENANT_CONTEXT_URL)

    assert response.status_code == 401
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_tenant_route_unknown_session_clears_stale_cookies(client):
    client.cookies.set(SESSION_COOKIE_NAME, "this-token-was-never-issued")
    client.cookies.set(CSRF_COOKIE_NAME, "some-stale-csrf-value")

    response = client.get(TENANT_CONTEXT_URL)

    assert response.status_code == 401
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_tenant_route_inactive_account_session_clears_stale_cookies(
    client, app, auth_tenancy, db_session
):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    _select_clinic(client, auth_tenancy.tenant_a.id)
    db_session.execute(
        text("UPDATE user_accounts SET status = 'inactive' WHERE id = :user_id"),
        {"user_id": str(auth_tenancy.owner_user.id)},
    )
    db_session.flush()
    db_session.expire_all()

    response = client.get(TENANT_CONTEXT_URL)

    assert response.status_code == 401
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_tenant_route_missing_cookie_is_plain_unauthorized(client):
    """No session cookie was ever sent - there is nothing stale to clear,
    and a plain 401 (no forced clearing) is the correct response even
    though development identity is enabled for this test app, since no
    dev headers are sent here either."""
    response = client.get(TENANT_CONTEXT_URL)

    assert response.status_code == 401
    assert _cookie_clear_header(response, SESSION_COOKIE_NAME) is None
    assert _cookie_clear_header(response, CSRF_COOKIE_NAME) is None


def test_tenant_route_missing_cookie_with_valid_dev_identity_still_works(client, auth_tenancy):
    """The retained development-identity fallback must still work when
    there is genuinely no session cookie at all - this fix only changes
    behavior for a cookie that was actually sent and turned out stale."""
    response = client.get(
        TENANT_CONTEXT_URL,
        headers=dev_headers(auth_tenancy.owner_user.id, auth_tenancy.tenant_a.id),
    )

    assert response.status_code == 200
    assert response.json()["tenant_id"] == str(auth_tenancy.tenant_a.id)


def test_tenant_route_invalid_session_does_not_fall_back_to_dev_identity(
    client, app, auth_tenancy, db_session
):
    """The core of this finding: an invalid/stale PRODUCTION session
    cookie must never be silently treated as absent just because valid
    dev-identity headers were also sent - it must still 401 and clear
    cookies, not quietly succeed via the dev-identity path."""
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    db_session.execute(
        text("UPDATE auth_sessions SET revoked_at = now() WHERE user_id = :user_id"),
        {"user_id": str(auth_tenancy.owner_user.id)},
    )
    db_session.flush()

    response = client.get(
        TENANT_CONTEXT_URL,
        headers=dev_headers(auth_tenancy.owner_user.id, auth_tenancy.tenant_a.id),
    )

    assert response.status_code == 401
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_tenant_route_valid_session_takes_priority_over_dev_headers(client, app, auth_tenancy):
    """A valid production session always wins - dev headers pointing at
    a completely different (and otherwise inaccessible) tenant must be
    ignored entirely, not merged with or preferred over the session."""
    _override_generous_rate_limiter(app)
    _login(
        client, auth_tenancy.dual_clinic_user.normalized_email, auth_tenancy.dual_clinic_password
    )
    _select_clinic(client, auth_tenancy.tenant_b.id)

    response = client.get(
        TENANT_CONTEXT_URL,
        headers=dev_headers(auth_tenancy.no_membership_user.id, auth_tenancy.tenant_a.id),
    )

    assert response.status_code == 200
    assert response.json()["tenant_id"] == str(auth_tenancy.tenant_b.id)


def test_tenant_route_stale_session_response_never_logs_the_raw_token(client, caplog):
    client.cookies.set(SESSION_COOKIE_NAME, "this-token-was-never-issued")

    with caplog.at_level(logging.DEBUG):
        response = client.get(TENANT_CONTEXT_URL)

    assert response.status_code == 401
    for record in caplog.records:
        assert "this-token-was-never-issued" not in record.getMessage()
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


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


def test_missing_csrf_blocks_mutation(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    response = client.post(LOGOUT_URL)  # no X-CSRF-Token header
    assert response.status_code == 403


def test_invalid_csrf_blocks_mutation(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    response = client.post(LOGOUT_URL, headers={"X-CSRF-Token": "not-the-real-token"})
    assert response.status_code == 403


def test_valid_csrf_allows_mutation(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    response = client.post(LOGOUT_URL, headers=_csrf_headers(client))
    assert response.status_code == 200


def _override_strict_rate_limiter(app, max_attempts: int = 1) -> None:
    # The store must be created ONCE and captured by the closure, not
    # inside the lambda body - FastAPI calls the override callable fresh
    # on every request, so a store instantiated inside the lambda would
    # never accumulate a count across requests.
    store = _FakeStore()
    app.dependency_overrides[get_login_rate_limiter] = lambda: RateLimiter(
        store, max_attempts=max_attempts, window_seconds=900
    )


def test_rate_limiting_returns_429(client, app, auth_tenancy):
    _override_strict_rate_limiter(app, max_attempts=1)
    _login(client, auth_tenancy.owner_user.normalized_email, "the wrong passphrase!!")
    response = _login(client, auth_tenancy.owner_user.normalized_email, "the wrong passphrase!!")

    assert response.status_code == 429


def test_rate_limiting_does_not_reveal_account_existence(client, app, auth_tenancy):
    _override_strict_rate_limiter(app, max_attempts=1)
    _login(client, auth_tenancy.owner_user.normalized_email, "the wrong passphrase!!")
    real_response = _login(
        client, auth_tenancy.owner_user.normalized_email, "the wrong passphrase!!"
    )

    client.cookies.clear()
    _override_strict_rate_limiter(app, max_attempts=1)
    _login(client, "nobody-at-all@auth.test", "some random passphrase!!")
    fake_response = _login(client, "nobody-at-all@auth.test", "some random passphrase!!")

    assert real_response.status_code == fake_response.status_code == 429
    assert real_response.json() == fake_response.json()


def test_change_password_requires_current_password(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    response = client.post(
        CHANGE_PASSWORD_URL,
        json={
            "current_password": "the wrong current password!!",
            "new_password": "a brand new passphrase!!",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 401


def test_change_password_revokes_other_sessions(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    other_client_session_cookie = client.cookies.get(SESSION_COOKIE_NAME)

    client.cookies.clear()
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    client.post(
        CHANGE_PASSWORD_URL,
        json={
            "current_password": auth_tenancy.owner_password,
            "new_password": "a brand new passphrase!!",
        },
        headers=_csrf_headers(client),
    )

    client.cookies.set(SESSION_COOKIE_NAME, other_client_session_cookie)
    response = client.get(ME_URL)
    assert response.status_code == 401


def test_password_reset_request_is_neutral(client, auth_tenancy):
    real = client.post(
        PASSWORD_RESET_REQUEST_URL, json={"email": auth_tenancy.owner_user.normalized_email}
    )
    fake = client.post(PASSWORD_RESET_REQUEST_URL, json={"email": "nobody-at-all@auth.test"})

    assert real.status_code == fake.status_code == 200
    assert real.json() == fake.json()


def test_password_reset_request_never_returns_a_token(client, auth_tenancy):
    response = client.post(
        PASSWORD_RESET_REQUEST_URL, json={"email": auth_tenancy.owner_user.normalized_email}
    )
    assert "token" not in response.text.lower()


def test_dev_headers_fail_outside_development(client, app, auth_tenancy):
    def _production_settings() -> Settings:
        return Settings(environment="production", development_identity_enabled=False)

    app.dependency_overrides[get_settings] = _production_settings
    response = client.get(
        "/api/v1/tenant-context",
        headers={
            "X-Dev-User-Id": str(auth_tenancy.owner_user.id),
            "X-Tenant-Id": str(auth_tenancy.tenant_a.id),
        },
    )
    assert response.status_code == 401


def test_dev_headers_never_override_a_production_session(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    client.post(
        SELECT_CLINIC_URL,
        json={"tenant_id": str(auth_tenancy.tenant_a.id)},
        headers=_csrf_headers(client),
    )

    # Dev headers claim a completely different (nonexistent) identity -
    # if they were honored, this would either 404 or resolve to a
    # different tenant; since a valid session takes priority, the request
    # must resolve using the session's OWN selected tenant, exactly as if
    # the dev headers were never sent.
    response = client.get(
        "/api/v1/tenant-context",
        headers={
            "X-Dev-User-Id": "11111111-1111-1111-1111-111111111111",
            "X-Tenant-Id": "22222222-2222-2222-2222-222222222222",
        },
    )
    assert response.status_code == 200
    assert response.json()["tenant_id"] == str(auth_tenancy.tenant_a.id)


def test_login_success_audit_event_contains_no_secrets(client, app, auth_tenancy, caplog):
    _override_generous_rate_limiter(app)
    with caplog.at_level(logging.INFO, logger="audit"):
        _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    events = [r.audit_event for r in caplog.records if hasattr(r, "audit_event")]
    assert any(e["event_type"] == "auth.login_success" for e in events)
    for event in events:
        assert auth_tenancy.owner_password not in str(event)
        assert auth_tenancy.owner_user.password_hash not in str(event)


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


def test_invalid_password_reset_token_does_not_clear_unrelated_auth_cookies(client):
    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={
            "token": "this-reset-token-was-never-issued",
            "new_password": "a brand new password!",
        },
    )

    assert response.status_code == 400
    assert _cookie_clear_header(response, SESSION_COOKIE_NAME) is None
    assert _cookie_clear_header(response, CSRF_COOKIE_NAME) is None
