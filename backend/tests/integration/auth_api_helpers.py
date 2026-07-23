"""Shared helpers for the auth API integration test suite (split across
`test_auth_*_api.py` files - see ARCHITECTURE.md for why: a single
~1200-line `test_auth_api.py` file was large enough that the Codex review
packet builder (`.ai-workflow/scripts/run-review.ps1`) dropped its
full-content section under its per-packet size budget, producing an
incomplete review. Splitting by domain keeps each file well under that
budget while keeping every test's name, intent, and assertions
unchanged - this module exists purely to avoid duplicating the request/
cookie helpers and URL constants across every split file.

Nothing here is a fixture - these are plain helper functions/constants a
test calls directly, not something pytest injects. Keeping them as plain
functions (not `conftest.py` fixtures) avoids implying they need
per-test setup/teardown they don't actually have.
"""

from app.core.rate_limit import RateLimiter, get_login_rate_limiter

LOGIN_URL = "/api/v1/auth/login"
LOGOUT_URL = "/api/v1/auth/logout"
ME_URL = "/api/v1/auth/me"
CLINICS_URL = "/api/v1/auth/clinics"
SELECT_CLINIC_URL = "/api/v1/auth/select-clinic"
CHANGE_PASSWORD_URL = "/api/v1/auth/change-password"
PASSWORD_RESET_REQUEST_URL = "/api/v1/auth/password-reset/request"
TENANT_CONTEXT_URL = "/api/v1/tenant-context"

CSRF_COOKIE_NAME = "csrf_token"


class FakeRateLimitStore:
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


def override_generous_rate_limiter(app) -> None:
    store = FakeRateLimitStore()
    app.dependency_overrides[get_login_rate_limiter] = lambda: RateLimiter(
        store, max_attempts=1000, window_seconds=900
    )


def override_strict_rate_limiter(app, max_attempts: int = 1) -> None:
    # The store must be created ONCE and captured by the closure, not
    # inside the lambda body - FastAPI calls the override callable fresh
    # on every request, so a store instantiated inside the lambda would
    # never accumulate a count across requests.
    store = FakeRateLimitStore()
    app.dependency_overrides[get_login_rate_limiter] = lambda: RateLimiter(
        store, max_attempts=max_attempts, window_seconds=900
    )


def login(client, email: str, password: str):
    return client.post(LOGIN_URL, json={"email": email, "password": password})


def csrf_headers(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("csrf_token")}


def select_clinic(client, tenant_id) -> None:
    response = client.post(
        SELECT_CLINIC_URL, json={"tenant_id": str(tenant_id)}, headers=csrf_headers(client)
    )
    assert response.status_code == 200


def set_cookie_headers(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def cookie_clear_header(response, name: str) -> str | None:
    for header in set_cookie_headers(response):
        if header.startswith(f"{name}="):
            return header
    return None


def assert_cookie_cleared(response, name: str) -> None:
    header = cookie_clear_header(response, name)
    assert header is not None, f"expected a Set-Cookie header clearing {name!r}"
    lowered = header.lower()
    assert "max-age=0" in lowered
    assert "path=/" in lowered
    # The cleared cookie's own value must never carry the raw token.
    value = header.split(";", 1)[0].split("=", 1)[1]
    assert value in ("", '""')
