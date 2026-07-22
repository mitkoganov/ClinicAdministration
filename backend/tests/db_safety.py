"""Fail-closed safety guard for the destructive test-database fixture in
conftest.py.

Test code must never be able to `drop_all`/`create_all` against a
developer's real DATABASE_URL. This module is intentionally standalone and
does not touch the database or import anything from `app.core.config` -
callers can unit-test every rejection path with zero DB connection and zero
risk.

Same-target comparison is canonical, not a raw string/tuple comparison:
`localhost` / `127.0.0.1` / `::1`, an omitted PostgreSQL port vs. an
explicit `5432`, and driver-suffix or query-string differences must all be
recognized as the same underlying database target. Getting this wrong in
the "different" direction is the dangerous failure mode (it would let a
destructive reset run against a developer's real database), so every path
that cannot conclusively prove two targets are different raises
`UnsafeTestDatabaseError` instead of silently treating them as distinct.
"""

import ipaddress
import os
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import unquote

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

_TEST_DATABASE_URL_VAR = "TEST_DATABASE_URL"
_APP_DATABASE_URL_VAR = "DATABASE_URL"
_ALLOW_DESTRUCTIVE_VAR = "ALLOW_DESTRUCTIVE_TEST_DB_RESET"

_LOOPBACK_HOST_TOKEN = "<loopback>"

# Default port applied only when a URL omits one. Dialects not listed here
# must fail closed rather than silently proceed with an unverified port.
_DEFAULT_PORTS_BY_BACKEND = {"postgresql": 5432}

Resolver = Callable[[str], str]


class UnsafeTestDatabaseError(RuntimeError):
    """Raised whenever the configured test database target is not
    conclusively safe to run destructive setup (drop_all/create_all)
    against. Callers must never catch this to fall back to another URL -
    it means "stop", not "try something else"."""


@dataclass(frozen=True)
class CanonicalDatabaseTarget:
    """The parts of a database URL that determine *which physical
    database* it points at. Username/password/driver-suffix/query-string
    are intentionally excluded: two URLs that differ only in those respects
    still point at the same database and must still compare as equal, since
    that is exactly the alias-bypass this guard exists to catch."""

    backend: str
    host: str
    port: int
    database: str


def _redact(url: str) -> str:
    """Renders `url` for use in an exception message with any password
    removed, without ever echoing the raw input back verbatim."""
    try:
        return make_url(url).render_as_string(hide_password=True)
    except ArgumentError:
        return re.sub(r"://[^@/\s]*@", "://***@", url)


def _normalize_backend(drivername: str) -> str:
    return drivername.split("+", 1)[0].strip().lower()


def _is_loopback_literal(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _default_resolver(host: str) -> str:
    return socket.gethostbyname(host)


def _canonical_host(host: str, resolver: Resolver) -> str:
    normalized = host.strip().lower().strip("[]")
    if not normalized:
        return normalized
    if _is_loopback_literal(normalized):
        return _LOOPBACK_HOST_TOKEN

    # Not a loopback literal - resolve so that a hostname alias for the
    # same machine (or for the real app database host) cannot slip past a
    # literal-string comparison. A resolution failure must never be read as
    # proof the two hosts differ, so it is treated as unsafe/ambiguous
    # (reject) rather than compared as a literal string.
    try:
        resolved = resolver(normalized)
    except OSError as exc:
        raise UnsafeTestDatabaseError(
            f"Could not resolve host {host!r} to confirm it is not the "
            "application database host. Refusing to treat an unresolvable "
            "hostname as safely distinct."
        ) from exc

    try:
        if ipaddress.ip_address(resolved).is_loopback:
            return _LOOPBACK_HOST_TOKEN
    except ValueError:
        pass
    return resolved


def _canonicalize(url: str, resolver: Resolver) -> CanonicalDatabaseTarget:
    try:
        parsed = make_url(url)
    except ArgumentError as exc:
        raise UnsafeTestDatabaseError(f"{_redact(url)} is not a valid database URL.") from exc

    backend = _normalize_backend(parsed.drivername)
    database = unquote(parsed.database or "")
    if not database:
        raise UnsafeTestDatabaseError(f"{_redact(url)} has no database name in its path.")

    port = parsed.port
    if port is None:
        port = _DEFAULT_PORTS_BY_BACKEND.get(backend)
        if port is None:
            raise UnsafeTestDatabaseError(
                f"{_redact(url)} omits a port and {backend!r} is not a dialect this "
                "guard knows a safe default port for. Specify the port explicitly."
            )

    host = _canonical_host(parsed.host or "", resolver)
    return CanonicalDatabaseTarget(backend=backend, host=host, port=port, database=database)


def targets_are_equivalent(
    url_a: str, url_b: str, *, resolver: Resolver = _default_resolver
) -> bool:
    """True if `url_a` and `url_b` resolve to the same canonical database
    target (same backend/host/port/database, ignoring driver suffix,
    credentials, and query-string formatting)."""
    return _canonicalize(url_a, resolver) == _canonicalize(url_b, resolver)


def _looks_like_test_database_name(db_name: str) -> bool:
    name = db_name.lower()
    return bool(name) and (name.endswith("_test") or "test" in name)


def assert_safe_test_database_url(
    url: str | None,
    app_database_url: str | None,
    *,
    resolver: Resolver = _default_resolver,
) -> str:
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

    target = _canonicalize(url, resolver)

    if app_database_url:
        app_target = _canonicalize(app_database_url, resolver)
        if target == app_target:
            raise UnsafeTestDatabaseError(
                f"{_TEST_DATABASE_URL_VAR} resolves to the exact same database target as "
                f"{_APP_DATABASE_URL_VAR} ({target.database!r} on {target.host}:{target.port}, "
                f"backend {target.backend!r}) - including equivalent host aliases and default "
                "ports. Refusing to run destructive setup against what may be a real "
                "development database."
            )

    if not _looks_like_test_database_name(target.database):
        raise UnsafeTestDatabaseError(
            f"Database name {target.database!r} does not look like a disposable test "
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


def _main(argv: list[str]) -> int:
    """Narrow CLI entry point so non-Python callers (the PowerShell test
    wrapper) can reuse this module's canonicalization as the single source
    of truth instead of re-implementing it. Never prints either input URL
    verbatim - only the redacted form appears in diagnostics.

    Exit codes: 0 = confirmed distinct targets (safe to proceed); 1 =
    equivalent or ambiguous/unresolvable (unsafe - caller must refuse)."""
    if len(argv) != 2:
        print("usage: python -m tests.db_safety <url_a> <url_b>", file=__import__("sys").stderr)
        return 1
    try:
        if targets_are_equivalent(argv[0], argv[1]):
            print(f"EQUIVALENT: {_redact(argv[0])} == {_redact(argv[1])}")
            return 1
        print("DISTINCT")
        return 0
    except UnsafeTestDatabaseError as exc:
        print(f"AMBIGUOUS: {exc}")
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
