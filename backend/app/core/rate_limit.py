"""Minimal Redis-backed login-throttling rate limiter.

Not a general-purpose distributed rate-limiting platform - one narrow
job: bound the number of login attempts for a given key within a fixed
time window, so brute-force guessing is slow without ever becoming a
permanent denial of service for a legitimate user.

Fail-open by design: if Redis is unavailable, `check_and_consume` allows
the attempt through (logging a warning) rather than blocking every login
in the platform on a Redis outage - the explicit tradeoff task.md asks
for ("ясно fail behavior при Redis недостъпност", "без permanent denial
of service"). This never reveals to the caller whether the failure was a
rate limit or a Redis outage - both are transparent to the caller and
audited only server-side.
"""

import logging
from functools import lru_cache
from typing import Protocol

import redis as redis_lib
from fastapi import Depends

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class RateLimitStore(Protocol):
    """The minimal Redis surface this module needs - lets tests inject a
    deterministic fake store instead of a real Redis connection."""

    def incr(self, key: str) -> int: ...
    def expire(self, key: str, seconds: int) -> object: ...
    def ttl(self, key: str) -> int: ...
    def delete(self, key: str) -> object: ...


class RateLimiter:
    def __init__(self, store: RateLimitStore, *, max_attempts: int, window_seconds: int) -> None:
        self._store = store
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds

    def check_and_consume(self, key: str) -> bool:
        """Returns True if the attempt is allowed (and records it), False
        if `key` has already hit the limit within the current window.
        Fails open (returns True, logs a warning) on any store error."""
        try:
            count = self._store.incr(key)
            if count == 1:
                # First hit in this window - start the TTL. A race between
                # two concurrent first-attempts both seeing count==1 and
                # each calling expire() is harmless: both set the same TTL.
                self._store.expire(key, self._window_seconds)
            return count <= self._max_attempts
        except Exception:
            logger.warning("Rate limiter store unavailable - failing open for key=%r", key)
            return True

    def reset(self, key: str) -> None:
        """Called on a successful login to stop a legitimate user's own
        future attempts from being penalized by earlier failed ones."""
        try:
            self._store.delete(key)
        except Exception:
            logger.warning("Rate limiter store unavailable while resetting key=%r", key)


@lru_cache
def _get_redis_client(redis_url: str) -> redis_lib.Redis:
    # Short, bounded timeouts so an unreachable Redis fails fast into the
    # fail-open path above, instead of hanging every login request.
    return redis_lib.Redis.from_url(
        redis_url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1
    )


def get_login_rate_limiter(settings: Settings = Depends(get_settings)) -> RateLimiter:
    """FastAPI dependency - tests override this to inject a deterministic
    fake store instead of a real Redis connection."""
    client = _get_redis_client(settings.redis_url)
    return RateLimiter(
        client,
        max_attempts=settings.login_rate_limit_max_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
    )


def login_rate_limit_keys(normalized_email: str, client_ip: str | None) -> list[str]:
    """Two independent keys, both must pass: one keyed on the account
    identifier (stops guessing many passwords for one account) and one on
    the network origin (stops one source guessing many accounts) - see
    task.md "Rate limit по: normalized account identifier; безопасен
    network key." Neither key alone is suffient against both attack
    shapes."""
    keys = [f"login-throttle:account:{normalized_email}"]
    if client_ip:
        keys.append(f"login-throttle:ip:{client_ip}")
    return keys
