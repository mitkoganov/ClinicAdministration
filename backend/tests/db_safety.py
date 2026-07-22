"""Fail-closed safety guard for the destructive test-database fixture in
conftest.py.

Test code must never be able to `drop_all`/`create_all` against a
developer's real DATABASE_URL. This module is intentionally standalone and
does not touch the database or import anything from `app.core.config` -
callers can unit-test every rejection path with zero DB connection and zero
risk.
"""

import os
from urllib.parse import urlsplit

_TEST_DATABASE_URL_VAR = "TEST_DATABASE_URL"
_APP_DATABASE_URL_VAR = "DATABASE_URL"
_ALLOW_DESTRUCTIVE_VAR = "ALLOW_DESTRUCTIVE_TEST_DB_RESET"


class UnsafeTestDatabaseError(RuntimeError):
    """Raised whenever the configured test database target is not
    conclusively safe to run destructive setup (drop_all/create_all)
    against. Callers must never catch this to fall back to another URL -
    it means "stop", not "try something else"."""


def _parse_target(url: str) -> tuple[str, int | None, str]:
    parsed = urlsplit(url)
    db_name = (parsed.path or "").lstrip("/")
    return (parsed.hostname or "", parsed.port, db_name)


def _looks_like_test_database_name(db_name: str) -> bool:
    name = db_name.lower()
    return bool(name) and (name.endswith("_test") or "test" in name)


def assert_safe_test_database_url(url: str | None, app_database_url: str | None) -> str:
    """Validates that `url` is safe to run destructive setup against.
    Returns `url` unchanged on success; raises `UnsafeTestDatabaseError`
    otherwise. Every rejection happens before any database connection is
    opened by the caller."""
    if not url or not url.strip():
        raise UnsafeTestDatabaseError(
            f"{_TEST_DATABASE_URL_VAR} is not set (or is empty). Destructive test "
            "setup requires an explicit, dedicated test database - it is never "
            "inferred or defaulted from the application DATABASE_URL."
        )

    target_host, target_port, target_db = _parse_target(url)
    if not target_db:
        raise UnsafeTestDatabaseError(
            f"{_TEST_DATABASE_URL_VAR}={url!r} has no database name in its path."
        )

    if app_database_url:
        app_host, app_port, app_db = _parse_target(app_database_url)
        if (target_host, target_port, target_db) == (app_host, app_port, app_db):
            raise UnsafeTestDatabaseError(
                f"{_TEST_DATABASE_URL_VAR} resolves to the exact same host/port/database "
                f"as {_APP_DATABASE_URL_VAR} ({target_db!r} on {target_host}:{target_port}). "
                "Refusing to run destructive setup against what may be a real "
                "development database."
            )

    if not _looks_like_test_database_name(target_db):
        raise UnsafeTestDatabaseError(
            f"Database name {target_db!r} does not look like a disposable test "
            "database (must end with '_test' or contain 'test')."
        )

    if os.environ.get(_ALLOW_DESTRUCTIVE_VAR, "").strip().lower() != "true":
        raise UnsafeTestDatabaseError(
            f"{_ALLOW_DESTRUCTIVE_VAR} is not set to 'true'. A test-like database "
            "name alone is not sufficient - destructive setup requires the "
            "explicit opt-in as well."
        )

    return url


def get_test_database_url() -> str:
    """Reads TEST_DATABASE_URL and DATABASE_URL directly from the process
    environment (not from app.core.config.get_settings, which is
    lru_cached and production-oriented) and returns the test URL only if
    it passes every safety check. Never falls back to DATABASE_URL."""
    test_url = os.environ.get(_TEST_DATABASE_URL_VAR)
    app_url = os.environ.get(_APP_DATABASE_URL_VAR)
    return assert_safe_test_database_url(test_url, app_url)
