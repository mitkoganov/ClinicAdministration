import pytest

from tests.db_safety import (
    UnsafeTestDatabaseError,
    assert_safe_test_database_url,
    get_test_database_url,
)

APP_URL = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic_admin"
VALID_TEST_URL = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic_admin_test"


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
    with pytest.raises(UnsafeTestDatabaseError, match="same host/port/database"):
        assert_safe_test_database_url(APP_URL, APP_URL)


def test_app_database_url_reused_with_test_like_name_is_still_rejected(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_DB_RESET", "true")
    app_url_test_named = "postgresql+psycopg://clinic:clinic@127.0.0.1:5433/clinic_admin_test"
    with pytest.raises(UnsafeTestDatabaseError, match="same host/port/database"):
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
