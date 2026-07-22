from app.core.rate_limit import RateLimiter, login_rate_limit_keys


class _FakeStore:
    """Deterministic in-memory stand-in for Redis - no TTL expiry logic
    needed since these tests never advance a clock, only exercise the
    incr/expire/delete call pattern itself."""

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


class _BrokenStore:
    def incr(self, key: str) -> int:
        raise ConnectionError("simulated Redis outage")

    def expire(self, key: str, seconds: int) -> None:
        raise ConnectionError("simulated Redis outage")

    def ttl(self, key: str) -> int:
        raise ConnectionError("simulated Redis outage")

    def delete(self, key: str) -> None:
        raise ConnectionError("simulated Redis outage")


def test_allows_attempts_under_the_limit():
    limiter = RateLimiter(_FakeStore(), max_attempts=3, window_seconds=60)
    assert limiter.check_and_consume("k") is True
    assert limiter.check_and_consume("k") is True
    assert limiter.check_and_consume("k") is True


def test_blocks_attempts_over_the_limit():
    limiter = RateLimiter(_FakeStore(), max_attempts=3, window_seconds=60)
    for _ in range(3):
        limiter.check_and_consume("k")
    assert limiter.check_and_consume("k") is False


def test_reset_clears_the_counter():
    store = _FakeStore()
    limiter = RateLimiter(store, max_attempts=1, window_seconds=60)
    limiter.check_and_consume("k")
    assert limiter.check_and_consume("k") is False
    limiter.reset("k")
    assert limiter.check_and_consume("k") is True


def test_different_keys_are_independent():
    limiter = RateLimiter(_FakeStore(), max_attempts=1, window_seconds=60)
    assert limiter.check_and_consume("a") is True
    assert limiter.check_and_consume("b") is True


def test_fails_open_when_the_store_is_unavailable():
    limiter = RateLimiter(_BrokenStore(), max_attempts=1, window_seconds=60)
    assert limiter.check_and_consume("k") is True
    assert limiter.check_and_consume("k") is True


def test_reset_does_not_raise_when_the_store_is_unavailable():
    limiter = RateLimiter(_BrokenStore(), max_attempts=1, window_seconds=60)
    limiter.reset("k")  # must not raise


def test_login_rate_limit_keys_include_account_and_ip():
    keys = login_rate_limit_keys("alice@example.com", "203.0.113.5")
    assert any("alice@example.com" in k for k in keys)
    assert any("203.0.113.5" in k for k in keys)
    assert len(keys) == 2


def test_login_rate_limit_keys_without_ip():
    keys = login_rate_limit_keys("alice@example.com", None)
    assert len(keys) == 1
