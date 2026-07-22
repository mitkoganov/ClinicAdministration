import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_rejects_invalid_database_url_scheme(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://clinic:clinic@localhost:3306/clinic_admin")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_default_construction():
    settings = Settings()
    assert settings.environment == "development"


def test_settings_reads_overrides_from_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    settings = Settings()
    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"


def test_settings_ignores_unrelated_env_file_vars(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("UNRELATED_VAR=value\n")
    settings = Settings()
    assert settings.environment == "development"


def test_settings_rejects_insecure_cookie_outside_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_allows_insecure_cookie_in_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    settings = Settings()
    assert settings.session_cookie_secure is False


def test_settings_defaults_to_secure_cookie():
    settings = Settings()
    assert settings.session_cookie_secure is True
