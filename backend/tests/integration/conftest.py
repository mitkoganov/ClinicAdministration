import pytest

from app.core.config import Settings, get_settings

DEV_USER_HEADER = "X-Dev-User-Id"
DEV_TENANT_HEADER = "X-Tenant-Id"


@pytest.fixture
def app(app):
    """Overrides the base `app` fixture (same name, closer conftest wins)
    to enable the development identity provider for every integration test
    in this directory, without affecting unit tests or the pre-existing
    health/config/ready test suites."""

    def _override_get_settings() -> Settings:
        return Settings(environment="development", development_identity_enabled=True)

    app.dependency_overrides[get_settings] = _override_get_settings
    return app


def dev_headers(user_id, tenant_id) -> dict[str, str]:
    return {DEV_USER_HEADER: str(user_id), DEV_TENANT_HEADER: str(tenant_id)}
