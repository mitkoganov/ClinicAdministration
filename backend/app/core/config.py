from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SUPPORTED_DB_SCHEMES = ("postgresql+psycopg://", "postgresql+psycopg2://")


class Settings(BaseSettings):
    """Environment-driven application settings. No default touches a real
    external system; every value must be supplied via environment variables
    or `.env` in local development."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = Field(default="development")

    database_url: str = Field(
        default="postgresql+psycopg://clinic:clinic@localhost:5432/clinic_admin"
    )
    redis_url: str = Field(default="redis://localhost:6379/0")
    qdrant_url: str = Field(default="http://localhost:6333")

    log_level: str = Field(default="INFO")

    development_identity_enabled: bool = Field(default=False)

    # --- MED-004: production authentication -----------------------------
    # Server-side session lifetimes (see app.services.session_service).
    # Absolute lifetime is a hard cap regardless of activity; idle lifetime
    # expires a session that has been unused for this long even before the
    # absolute cap. Both are enforced on every request, fail-closed.
    session_absolute_lifetime_hours: int = Field(default=24 * 7)
    session_idle_lifetime_hours: int = Field(default=24)
    # Cookies are Secure by default and only relaxed for local plain-HTTP
    # development - never silently insecure in anything resembling
    # production. See app.core.session_cookies.
    session_cookie_secure: bool = Field(default=True)
    password_reset_token_lifetime_minutes: int = Field(default=30)
    invitation_token_lifetime_hours: int = Field(default=24 * 7)
    # Login throttling (see app.core.rate_limit) - a bounded window, never
    # a permanent lockout.
    login_rate_limit_max_attempts: int = Field(default=5)
    login_rate_limit_window_seconds: int = Field(default=15 * 60)

    @model_validator(mode="after")
    def _validate_cookie_security(self) -> "Settings":
        if not self.session_cookie_secure and self.environment != "development":
            raise ValueError(
                "session_cookie_secure=False requires environment='development'; "
                "refusing to start with an insecure (non-Secure) session cookie "
                f"outside local development under environment={self.environment!r}"
            )
        return self

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if not value.startswith(_SUPPORTED_DB_SCHEMES):
            raise ValueError(
                f"database_url must use one of {_SUPPORTED_DB_SCHEMES}, got: {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _validate_development_identity(self) -> "Settings":
        if self.development_identity_enabled and self.environment != "development":
            raise ValueError(
                "development_identity_enabled requires environment='development'; "
                "refusing to start with a non-production-safe identity provider "
                f"enabled under environment={self.environment!r}"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
