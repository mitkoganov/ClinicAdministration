import pytest

from app.core.config import Settings, get_settings
from tests.integration.auth_api_helpers import SELECT_CLINIC_URL
from tests.integration.auth_api_helpers import csrf_headers as _csrf_headers
from tests.integration.auth_api_helpers import login as _login
from tests.integration.auth_api_helpers import (
    override_generous_rate_limiter as _override_generous_rate_limiter,
)

pytestmark = pytest.mark.integration


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
