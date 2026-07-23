import pytest

from tests.integration.auth_api_helpers import LOGOUT_URL
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
