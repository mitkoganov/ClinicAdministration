import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.types import Uuid

from app.db.base import Base


class UserAccountStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class EmailVerificationState(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"


def normalize_email(value: str) -> str:
    """The single canonical normalization for login identifiers: trimmed,
    lower-cased. Deterministic, so the unique index on `normalized_email`
    is an effective case-insensitive uniqueness guarantee - two raw inputs
    differing only in case or surrounding whitespace collide."""
    return value.strip().lower()


class UserAccount(Base):
    """A production authentication identity: a person who can log in.
    Carries no tenant-specific role or permission - that lives entirely in
    `TenantMembership` rows (see app.models.membership). A `UserAccount`
    may have zero, one, or several active memberships; authentication
    (proving who you are) is deliberately independent from authorization
    (what you may do in a given clinic)."""

    __tablename__ = "user_accounts"
    __table_args__ = (Index("ix_user_accounts_normalized_email", "normalized_email", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Always the normalized form - see the `@validates` hook below, which
    # normalizes on every assignment/construction so callers never need to
    # normalize first themselves (mirrors app.models.tenant's slug pattern).
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Never a plaintext password. Argon2id encoded hashes are long but
    # bounded; 512 is generous headroom without inviting unbounded input.
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[UserAccountStatus] = mapped_column(
        SAEnum(
            UserAccountStatus,
            name="user_account_status",
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=UserAccountStatus.ACTIVE,
    )
    email_verification_state: Mapped[EmailVerificationState] = mapped_column(
        SAEnum(
            EmailVerificationState,
            name="email_verification_state",
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=EmailVerificationState.UNVERIFIED,
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
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @validates("normalized_email")
    def _validate_normalized_email(self, key: str, value: str) -> str:
        return normalize_email(value)

    def __repr__(self) -> str:
        # Deliberately excludes password_hash - a repr/debug dump must
        # never be a way to leak it (e.g. into a log line or exception
        # traceback that includes local variables).
        return f"UserAccount(id={self.id!r}, normalized_email={self.normalized_email!r})"
