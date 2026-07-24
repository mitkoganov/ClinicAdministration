from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.core.errors import ConflictError
from app.core.session_dependency import SESSION_COOKIE_NAME
from tests.integration.auth_api_helpers import CSRF_COOKIE_NAME, LOGOUT_URL, SELECT_CLINIC_URL
from tests.integration.auth_api_helpers import cookie_clear_header as _cookie_clear_header
from tests.integration.auth_api_helpers import csrf_headers as _csrf_headers
from tests.integration.auth_api_helpers import login as _login
from tests.integration.auth_api_helpers import (
    override_generous_rate_limiter as _override_generous_rate_limiter,
)

pytestmark = pytest.mark.integration


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


# --- Codex repair (HIGH): get_current_session_optional backs require_csrf
# - a transient failure while resolving the session there must never be
# collapsed into "no session, CSRF does not apply", or a mutating request
# could execute without ever having its CSRF token checked (the route's
# own separate get_current_session dependency might still resolve the
# session successfully on its own independent re-validation). -----------


def test_transient_session_touch_failure_blocks_mutation_without_skipping_csrf(
    client, app, auth_tenancy, db_session
):
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    # Force the idle-refresh touch to be due (TOUCH_THRESHOLD is 5 minutes)
    # - this is what require_csrf's session resolution hits on this request.
    db_session.execute(
        text("UPDATE auth_sessions SET last_seen_at = :past WHERE user_id = :user_id"),
        {
            "past": datetime.now(UTC) - timedelta(minutes=10),
            "user_id": str(auth_tenancy.owner_user.id),
        },
    )
    db_session.flush()

    with patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")):
        response = client.post(
            SELECT_CLINIC_URL,
            json={"tenant_id": str(auth_tenancy.tenant_a.id)},
            headers=_csrf_headers(client),
        )

    assert response.status_code != 200
    assert "simulated commit failure" not in response.text
    assert "RuntimeError" not in response.text
    # A transient failure is not a stale session - it must not trigger the
    # stale-cookie-clearing behavior reserved for a genuinely invalid one.
    assert _cookie_clear_header(response, SESSION_COOKIE_NAME) is None
    assert _cookie_clear_header(response, CSRF_COOKIE_NAME) is None

    db_session.expire_all()
    selected_tenant_id = db_session.execute(
        text("SELECT selected_tenant_id FROM auth_sessions WHERE user_id = :user_id"),
        {"user_id": str(auth_tenancy.owner_user.id)},
    ).scalar_one()
    assert selected_tenant_id is None


def test_transient_session_touch_failure_is_retryable(client, app, auth_tenancy, db_session):
    """Proves the failure above is genuinely transient: once the injected
    failure is gone, the identical request (same session, same CSRF
    token) completes normally."""
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    db_session.execute(
        text("UPDATE auth_sessions SET last_seen_at = :past WHERE user_id = :user_id"),
        {
            "past": datetime.now(UTC) - timedelta(minutes=10),
            "user_id": str(auth_tenancy.owner_user.id),
        },
    )
    db_session.flush()

    with patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")):
        first = client.post(
            SELECT_CLINIC_URL,
            json={"tenant_id": str(auth_tenancy.tenant_a.id)},
            headers=_csrf_headers(client),
        )
    assert first.status_code != 200

    retry = client.post(
        SELECT_CLINIC_URL,
        json={"tenant_id": str(auth_tenancy.tenant_a.id)},
        headers=_csrf_headers(client),
    )
    assert retry.status_code == 200


def test_generic_unauthorized_error_is_not_collapsed_by_optional_session_resolver(
    client, app, auth_tenancy
):
    """Defensive regression: get_current_session_optional's exception
    handling must stay fully removed - simulate an unrelated AppError
    from SessionService.validate_session (not something that happens in
    practice today) and confirm require_csrf's dependency resolution
    fails instead of treating it as an absent session."""
    _override_generous_rate_limiter(app)
    _login(client, auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password)

    with patch(
        "app.core.session_dependency.SessionService.validate_session",
        side_effect=ConflictError("simulated unrelated service error"),
    ):
        response = client.post(
            SELECT_CLINIC_URL,
            json={"tenant_id": str(auth_tenancy.tenant_a.id)},
            headers=_csrf_headers(client),
        )

    assert response.status_code == 409
    assert _cookie_clear_header(response, SESSION_COOKIE_NAME) is None
    assert _cookie_clear_header(response, CSRF_COOKIE_NAME) is None
