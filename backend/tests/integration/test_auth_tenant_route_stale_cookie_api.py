import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.session_dependency import SESSION_COOKIE_NAME
from tests.integration.auth_api_helpers import CSRF_COOKIE_NAME, TENANT_CONTEXT_URL
from tests.integration.auth_api_helpers import assert_cookie_cleared as _assert_cookie_cleared
from tests.integration.auth_api_helpers import cookie_clear_header as _cookie_clear_header
from tests.integration.auth_api_helpers import login as _login
from tests.integration.auth_api_helpers import (
    override_generous_rate_limiter as _override_generous_rate_limiter,
)
from tests.integration.auth_api_helpers import select_clinic as _select_clinic
from tests.integration.conftest import dev_headers

pytestmark = pytest.mark.integration


# --- MED-004 repair: get_current_session_optional must not swallow a
# stale (rather than simply missing) session cookie for tenant-scoped
# routes that combine session auth with the dev-identity fallback (see
# app.core.tenant_context.get_tenant_context). ------------------------


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
