import pytest

from tests.db_safety import (
    UnsafeTestDatabaseError,
    assert_safe_test_database_url,
    get_test_database_url,
    targets_are_equivalent,
)

APP_URL = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic_admin"
VALID_TEST_URL = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic_admin_test"


def _raising_resolver(host):
    raise OSError(f"simulated resolution failure for {host}")


def _echo_resolver(host):
    # Deterministic stand-in for socket.gethostbyname in tests: a real
    # non-loopback DNS lookup must never be required for this suite to
    # pass. Returns the host unchanged - fine because none of the tests
    # exercising this resolver rely on resolving to a *different* literal.
    return host


def test_missing_test_database_url_is_rejected(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    with pytest.raises(UnsafeTestDatabaseError, match="not set"):
        assert_safe_test_database_url(None, APP_URL)


def test_empty_test_database_url_is_rejected(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    with pytest.raises(UnsafeTestDatabaseError, match="not set"):
        assert_safe_test_database_url("   ", APP_URL)


def test_non_test_database_name_is_rejected(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    non_test_url = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/some_other_db"
    with pytest.raises(UnsafeTestDatabaseError, match="does not look like a disposable test"):
        assert_safe_test_database_url(non_test_url, APP_URL)


def test_app_database_url_reused_as_test_database_url_is_rejected(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    # Even though "clinic_admin" doesn't look test-like, the same-as-app-db
    # check must fire first (and independently) of the name heuristic.
    with pytest.raises(UnsafeTestDatabaseError, match="same database target"):
        assert_safe_test_database_url(APP_URL, APP_URL)


def test_app_database_url_reused_with_test_like_name_is_still_rejected(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    app_url_test_named = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic_admin_test"
    with pytest.raises(UnsafeTestDatabaseError, match="same database target"):
        assert_safe_test_database_url(app_url_test_named, app_url_test_named)


def test_test_like_name_without_opt_in_is_rejected(monkeypatch):
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", raising=False)
    with pytest.raises(UnsafeTestDatabaseError, match="explicit opt-in"):
        assert_safe_test_database_url(VALID_TEST_URL, APP_URL)


def test_opt_in_without_test_like_name_is_rejected(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    non_test_url = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/some_other_db"
    with pytest.raises(UnsafeTestDatabaseError, match="does not look like a disposable test"):
        assert_safe_test_database_url(non_test_url, APP_URL)


def test_valid_disposable_test_database_is_accepted(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    assert assert_safe_test_database_url(VALID_TEST_URL, APP_URL) == VALID_TEST_URL


def test_valid_disposable_test_database_accepted_without_app_url_configured(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    assert assert_safe_test_database_url(VALID_TEST_URL, None) == VALID_TEST_URL


def test_opt_in_value_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "TRUE")
    assert assert_safe_test_database_url(VALID_TEST_URL, APP_URL) == VALID_TEST_URL


def test_get_test_database_url_reads_from_environment(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", VALID_TEST_URL)
    monkeypatch.setenv("DATABASE_URL", APP_URL)
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")

    assert get_test_database_url() == VALID_TEST_URL


# --- Canonical same-target comparison (URL-alias bypass fix) ---------------


def test_identical_raw_urls_are_rejected(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    with pytest.raises(UnsafeTestDatabaseError, match="same database target"):
        assert_safe_test_database_url(APP_URL, APP_URL)


def test_localhost_vs_127_0_0_1_is_rejected_as_same_target(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    app_url = "postgresql+psycopg://clinic:clinic@localhost:5433/clinic_admin"
    test_url = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic_admin"
    with pytest.raises(UnsafeTestDatabaseError, match="same database target"):
        assert_safe_test_database_url(test_url, app_url)


def test_localhost_vs_ipv6_loopback_is_rejected_as_same_target(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    app_url = "postgresql+psycopg://clinic:clinic@localhost:5433/clinic_admin"
    test_url = "postgresql+psycopg://clinic:clinic@[::1]:5433/clinic_admin"
    with pytest.raises(UnsafeTestDatabaseError, match="same database target"):
        assert_safe_test_database_url(test_url, app_url)


def test_omitted_port_vs_explicit_default_port_is_rejected_as_same_target(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    app_url = "postgresql+psycopg://clinic:clinic@127.0.0.1/clinic_admin"
    test_url = "postgresql+psycopg://clinic:clinic@127.0.0.1:5432/clinic_admin"
    with pytest.raises(UnsafeTestDatabaseError, match="same database target"):
        assert_safe_test_database_url(test_url, app_url)


def test_driver_suffix_difference_does_not_hide_the_same_target(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    app_url = "postgresql://clinic:clinic@127.0.0.1:5433/clinic_admin"
    test_url = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic_admin"
    with pytest.raises(UnsafeTestDatabaseError, match="same database target"):
        assert_safe_test_database_url(test_url, app_url)


def test_url_encoded_database_names_compare_correctly():
    encoded = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic%5Ftest"
    plain = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic_test"
    assert targets_are_equivalent(encoded, plain)


def test_query_parameter_ordering_does_not_create_a_false_difference():
    url_a = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic_test?sslmode=disable&application_name=a"
    url_b = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic_test?application_name=a&sslmode=disable"
    assert targets_are_equivalent(url_a, url_b)


def test_different_database_names_are_allowed_when_test_name_rule_is_satisfied(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    app_url = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic_admin"
    test_url = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic_admin_test"
    assert assert_safe_test_database_url(test_url, app_url) == test_url


def test_different_non_loopback_hosts_remain_distinct_when_safely_resolvable():
    url_a = "postgresql+psycopg://clinic:clinic@db-a.internal:5432/clinic_test"
    url_b = "postgresql+psycopg://clinic:clinic@db-b.internal:5432/clinic_test"
    assert not targets_are_equivalent(url_a, url_b, resolver=_echo_resolver)


def test_malformed_url_fails_closed():
    with pytest.raises(UnsafeTestDatabaseError):
        targets_are_equivalent("not-a-valid-url", VALID_TEST_URL)


def test_url_missing_database_name_fails_closed():
    with pytest.raises(UnsafeTestDatabaseError):
        targets_are_equivalent("postgresql+psycopg://clinic:clinic@127.0.0.1:5433/", VALID_TEST_URL)


def test_unresolvable_hostname_is_ambiguous_and_fails_closed():
    url_a = "postgresql+psycopg://clinic:clinic@does-not-resolve.invalid:5432/clinic_test"
    with pytest.raises(UnsafeTestDatabaseError, match="Could not resolve host"):
        targets_are_equivalent(url_a, VALID_TEST_URL, resolver=_raising_resolver)


def test_unknown_dialect_without_explicit_port_fails_closed():
    with pytest.raises(UnsafeTestDatabaseError, match="default port"):
        mysql_url = "mysql+pymysql://clinic:clinic@127.0.0.1/clinic_test"
        targets_are_equivalent(mysql_url, VALID_TEST_URL)


def test_password_never_appears_in_exception_messages(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    secret_app_url = "postgresql+psycopg://clinic:supersecret123@localhost:5433/clinic_admin"
    secret_test_url = "postgresql+psycopg://clinic:supersecret123@127.0.0.1:5433/clinic_admin"
    with pytest.raises(UnsafeTestDatabaseError) as excinfo:
        assert_safe_test_database_url(secret_test_url, secret_app_url)
    assert "supersecret123" not in str(excinfo.value)


def test_password_never_appears_in_malformed_url_message():
    secret_url = "postgresql+psycopg://clinic:supersecret123@127.0.0.1:5433/"
    with pytest.raises(UnsafeTestDatabaseError) as excinfo:
        targets_are_equivalent(secret_url, VALID_TEST_URL)
    assert "supersecret123" not in str(excinfo.value)
