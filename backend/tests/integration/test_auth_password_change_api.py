import pytest

from app.core.session_dependency import SESSION_COOKIE_NAME
from tests.integration.auth_api_helpers import CHANGE_PASSWORD_URL, ME_URL
from tests.integration.auth_api_helpers import csrf_headers as _csrf_headers
from tests.integration.auth_api_helpers import login as _login
from tests.integration.auth_api_helpers import (
    override_generous_rate_limiter as _override_generous_rate_limiter,
)

pytestmark = pytest.mark.integration


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
