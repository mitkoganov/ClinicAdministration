import pytest

from app.core.session_dependency import SESSION_COOKIE_NAME
from tests.integration.auth_api_helpers import CSRF_COOKIE_NAME, PASSWORD_RESET_REQUEST_URL
from tests.integration.auth_api_helpers import cookie_clear_header as _cookie_clear_header

pytestmark = pytest.mark.integration


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
