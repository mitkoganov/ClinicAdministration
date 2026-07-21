from functools import lru_cache

from pydantic import Field, field_validator
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

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if not value.startswith(_SUPPORTED_DB_SCHEMES):
            raise ValueError(
                f"database_url must use one of {_SUPPORTED_DB_SCHEMES}, got: {value!r}"
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
