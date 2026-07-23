import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.errors import ConflictError
from app.core.session_dependency import SESSION_COOKIE_NAME
from tests.integration.auth_api_helpers import (
    CSRF_COOKIE_NAME,
    LOGOUT_URL,
    ME_URL,
)
from tests.integration.auth_api_helpers import assert_cookie_cleared as _assert_cookie_cleared
from tests.integration.auth_api_helpers import cookie_clear_header as _cookie_clear_header
from tests.integration.auth_api_helpers import csrf_headers as _csrf_headers
from tests.integration.auth_api_helpers import login as _login
from tests.integration.auth_api_helpers import (
    override_generous_rate_limiter as _override_generous_rate_limiter,
)
from tests.integration.auth_api_helpers import (
    override_strict_rate_limiter as _override_strict_rate_limiter,
)
from tests.integration.conftest import dev_headers

pytestmark = pytest.mark.integration


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


def test_login_success_audit_event_contains_no_secrets(client, app, auth_tenancy, caplog):
    _override_generous_rate_limiter(app)
    with caplog.at_level(logging.INFO, logger="audit"):
        _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    events = [r.audit_event for r in caplog.records if hasattr(r, "audit_event")]
    assert any(e["event_type"] == "auth.login_success" for e in events)
    for event in events:
        assert auth_tenancy.owner_password not in str(event)
        assert auth_tenancy.owner_user.password_hash not in str(event)


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
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


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


# --- MED-004 repair: logout must stay idempotent (200 + cookie clearing)
# for every "no usable session" condition, never leaking which applied,
# while still requiring CSRF whenever there IS a genuinely valid session
# to revoke. --------------------------------------------------------


def test_logout_with_expired_session_is_idempotent(client, app, auth_tenancy, db_session):
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

    response = client.post(LOGOUT_URL)

    assert response.status_code == 200
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_logout_with_a_pre_revoked_session_is_idempotent(client, app, auth_tenancy, db_session):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    db_session.execute(
        text("UPDATE auth_sessions SET revoked_at = now() WHERE user_id = :user_id"),
        {"user_id": str(auth_tenancy.owner_user.id)},
    )
    db_session.flush()

    response = client.post(LOGOUT_URL)

    assert response.status_code == 200
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_logout_with_unknown_session_cookie_is_idempotent(client):
    client.cookies.set(SESSION_COOKIE_NAME, "this-token-was-never-issued")
    client.cookies.set(CSRF_COOKIE_NAME, "some-stale-csrf-value")

    response = client.post(LOGOUT_URL)

    assert response.status_code == 200
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_logout_with_malformed_session_cookie_is_idempotent(client):
    client.cookies.set(SESSION_COOKIE_NAME, "%%%not-a-real-token-shape%%%")

    response = client.post(LOGOUT_URL)

    assert response.status_code == 200
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_logout_with_inactive_account_session_is_idempotent(client, app, auth_tenancy, db_session):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    db_session.execute(
        text("UPDATE user_accounts SET status = 'inactive' WHERE id = :user_id"),
        {"user_id": str(auth_tenancy.owner_user.id)},
    )
    db_session.flush()
    db_session.expire_all()

    response = client.post(LOGOUT_URL)

    assert response.status_code == 200
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


def test_logout_with_valid_session_and_missing_csrf_is_rejected(
    client, app, auth_tenancy, db_session
):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    with patch("app.services.auth_service.emit_audit_event") as mock_emit_audit_event:
        response = client.post(LOGOUT_URL)

    assert response.status_code == 403
    assert _cookie_clear_header(response, SESSION_COOKIE_NAME) is None
    assert _cookie_clear_header(response, CSRF_COOKIE_NAME) is None
    assert mock_emit_audit_event.call_count == 0

    db_session.expire_all()
    session = db_session.execute(
        text("SELECT revoked_at FROM auth_sessions WHERE user_id = :user_id"),
        {"user_id": str(auth_tenancy.owner_user.id)},
    ).scalar_one()
    assert session is None
    assert client.get(ME_URL).status_code == 200


def test_logout_with_valid_session_and_invalid_csrf_is_rejected(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    response = client.post(LOGOUT_URL, headers={"X-CSRF-Token": "not-the-real-token"})

    assert response.status_code == 403
    assert _cookie_clear_header(response, SESSION_COOKIE_NAME) is None
    assert _cookie_clear_header(response, CSRF_COOKIE_NAME) is None
    assert client.get(ME_URL).status_code == 200


def test_logout_with_valid_session_and_valid_csrf_revokes_it(client, app, auth_tenancy, db_session):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    response = client.post(LOGOUT_URL, headers=_csrf_headers(client))

    assert response.status_code == 200
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)
    db_session.expire_all()
    revoked_at = db_session.execute(
        text("SELECT revoked_at FROM auth_sessions WHERE user_id = :user_id"),
        {"user_id": str(auth_tenancy.owner_user.id)},
    ).scalar_one()
    assert revoked_at is not None


# --- Codex repair (HIGH): a transient failure while persisting the
# session's own idle-refresh touch must never be collapsed into the
# same idempotent-success response reserved for a genuinely stale
# session - the session is still valid server-side, so a false 200
# here would tell the caller logout succeeded while it never revoked
# anything. --------------------------------------------------------


def test_logout_with_transient_touch_commit_failure_does_not_return_false_success(
    client, app, auth_tenancy, db_session
):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    # Force the idle-refresh touch to be due (TOUCH_THRESHOLD is 5 minutes).
    db_session.execute(
        text("UPDATE auth_sessions SET last_seen_at = :past WHERE user_id = :user_id"),
        {
            "past": datetime.now(UTC) - timedelta(minutes=10),
            "user_id": str(auth_tenancy.owner_user.id),
        },
    )
    db_session.flush()

    with patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")):
        response = client.post(LOGOUT_URL, headers=_csrf_headers(client))

    assert response.status_code != 200
    assert "simulated commit failure" not in response.text
    assert "RuntimeError" not in response.text
    assert _cookie_clear_header(response, SESSION_COOKIE_NAME) is None
    assert _cookie_clear_header(response, CSRF_COOKIE_NAME) is None

    db_session.expire_all()
    revoked_at = db_session.execute(
        text("SELECT revoked_at FROM auth_sessions WHERE user_id = :user_id"),
        {"user_id": str(auth_tenancy.owner_user.id)},
    ).scalar_one()
    assert revoked_at is None

    # The injected failure was transient - a retry (no longer patched)
    # must be able to complete a real, successful logout.
    retry_response = client.post(LOGOUT_URL, headers=_csrf_headers(client))
    assert retry_response.status_code == 200
    _assert_cookie_cleared(retry_response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(retry_response, CSRF_COOKIE_NAME)


def test_logout_propagates_a_non_session_apperror_without_collapsing_it(client, app, auth_tenancy):
    """Defensive regression: the logout resolver's except clause must
    stay narrowed to InvalidSessionError - simulate an unrelated AppError
    from SessionService.validate_session (not something that happens in
    practice today) and confirm it still is not collapsed into the same
    idempotent-success response reserved for a genuinely stale session."""
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    with patch(
        "app.core.session_dependency.SessionService.validate_session",
        side_effect=ConflictError("simulated unrelated service error"),
    ):
        response = client.post(LOGOUT_URL, headers=_csrf_headers(client))

    assert response.status_code == 409
    assert _cookie_clear_header(response, SESSION_COOKIE_NAME) is None
    assert _cookie_clear_header(response, CSRF_COOKIE_NAME) is None


def test_logout_stale_cookie_ignores_dev_headers(client, auth_tenancy):
    """A stale logout must stay idempotent (200, cookies cleared) even
    when valid dev-identity headers are also sent - dev headers are
    simply irrelevant to logout, not a way to change its outcome."""
    client.cookies.set(SESSION_COOKIE_NAME, "this-token-was-never-issued")

    response = client.post(
        LOGOUT_URL, headers=dev_headers(auth_tenancy.owner_user.id, auth_tenancy.tenant_a.id)
    )

    assert response.status_code == 200
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)


def test_logout_valid_session_still_requires_csrf_even_with_dev_headers(client, app, auth_tenancy):
    """A valid production session takes priority over dev headers for
    logout too, and still requires CSRF - dev headers cannot be used to
    bypass the CSRF requirement for a real session."""
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    response = client.post(
        LOGOUT_URL, headers=dev_headers(auth_tenancy.owner_user.id, auth_tenancy.tenant_a.id)
    )

    assert response.status_code == 403
    assert client.get(ME_URL).status_code == 200


def test_repeated_logout_after_success_stays_idempotent(client, app, auth_tenancy):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)
    first = client.post(LOGOUT_URL, headers=_csrf_headers(client))
    assert first.status_code == 200

    second = client.post(LOGOUT_URL)

    assert second.status_code == 200
    _assert_cookie_cleared(second, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(second, CSRF_COOKIE_NAME)


def test_logout_response_is_identical_for_every_unusable_session_reason(app):
    """Missing, expired, revoked, unknown, and malformed session cookies
    must all be externally indistinguishable via logout's response."""
    no_cookie_client = TestClient(app)
    no_cookie_response = no_cookie_client.post(LOGOUT_URL)

    unknown_client = TestClient(app)
    unknown_client.cookies.set(SESSION_COOKIE_NAME, "this-token-was-never-issued")
    unknown_response = unknown_client.post(LOGOUT_URL)

    malformed_client = TestClient(app)
    malformed_client.cookies.set(SESSION_COOKIE_NAME, "%%%not-a-real-token-shape%%%")
    malformed_response = malformed_client.post(LOGOUT_URL)

    responses = [no_cookie_response, unknown_response, malformed_response]
    for response in responses:
        assert response.status_code == 200

    bodies = [response.json() for response in responses]
    assert all(body == bodies[0] for body in bodies)


def test_logout_stale_session_never_logs_the_raw_token(client, caplog):
    client.cookies.set(SESSION_COOKIE_NAME, "this-token-was-never-issued")

    with caplog.at_level(logging.DEBUG):
        response = client.post(LOGOUT_URL)

    assert response.status_code == 200
    for record in caplog.records:
        assert "this-token-was-never-issued" not in record.getMessage()


def test_logout_fix_does_not_regress_tenant_route_stale_cookie_handling(client):
    """Sanity regression check: the logout-specific session resolver must
    not have weakened the shared get_current_session_optional path other
    routes rely on for stale-cookie clearing."""
    client.cookies.set(SESSION_COOKIE_NAME, "this-token-was-never-issued")

    response = client.get(ME_URL)

    assert response.status_code == 401
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, CSRF_COOKIE_NAME)


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
