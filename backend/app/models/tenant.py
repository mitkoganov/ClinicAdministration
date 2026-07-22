import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.types import Uuid

from app.core.slug import validate_slug
from app.db.base import Base

# Mirrors app.core.slug.validate_slug's rules at the database level: a
# second, independent enforcement layer so even a raw SQL insert bypassing
# the ORM cannot persist an empty/invalid slug. Keep in sync with
# app.core.slug._SLUG_PATTERN / MIN_SLUG_LENGTH if either changes.
_SLUG_CHECK_CONSTRAINT = "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(slug) >= 2"


class TenantStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Tenant(Base):
    """An independent organization using the platform. Carries no
    clinic-specific fields — those belong to a future `clinics` module."""

    __tablename__ = "tenants"
    __table_args__ = (CheckConstraint(_SLUG_CHECK_CONSTRAINT, name="ck_tenants_slug_valid"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Always the normalized form produced by app.core.slug.normalize_slug.
    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    status: Mapped[TenantStatus] = mapped_column(
        SAEnum(
            TenantStatus,
            name="tenant_status",
            native_enum=False,
            length=20,
            # Without this, SQLAlchemy persists the enum MEMBER NAME
            # ("ACTIVE") instead of its .value ("active") - store the
            # documented lowercase contract (task.md/ARCHITECTURE.md/
            # SECURITY.md), not the Python identifier.
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=TenantStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @validates("slug")
    def _validate_slug(self, key: str, value: str) -> str:
        # Application-layer enforcement (layer 1); the CHECK constraint
        # above is layer 2. Raises app.core.slug.InvalidSlugError - a
        # ValueError subclass - immediately on assignment/construction,
        # before any flush or commit.
        validate_slug(value)
        return value
