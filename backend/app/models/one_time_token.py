import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.models.membership import MembershipRole


class TokenPurpose(StrEnum):
    PASSWORD_RESET = "password_reset"
    INVITATION_ACCEPT = "invitation_accept"


class OneTimeToken(Base):
    """A purpose-bound, single-use token shared by the password-reset and
    staff-invitation-acceptance flows. Only `token_hash` (SHA-256 of the
    raw token - see app.core.session_tokens) is ever persisted; the raw
    token is generated, returned to the caller once, and never logged or
    stored anywhere.

    Field usage differs by `purpose`:
      * PASSWORD_RESET - `user_id` is required (the existing account being
        reset); `tenant_id`/`invited_role`/`invitee_email`/
        `inviter_user_id` are unused (NULL).
      * INVITATION_ACCEPT - `tenant_id`, `invited_role`, and
        `invitee_email` are required (the invitation's own context, which
        the accepting client can never override); `user_id` is NULL until
        an existing account accepts, `inviter_user_id` records who sent
        it.

    A single-table design (rather than two near-identical tables) keeps
    the single-use/expiry/consumption machinery
    (app.repositories.one_time_token) in one place for both purposes."""

    __tablename__ = "one_time_tokens"
    __table_args__ = (
        Index("ix_one_time_tokens_token_hash", "token_hash", unique=True),
        Index("ix_one_time_tokens_user_id", "user_id"),
        Index("ix_one_time_tokens_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[TokenPurpose] = mapped_column(
        SAEnum(
            TokenPurpose,
            name="one_time_token_purpose",
            native_enum=False,
            length=30,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user_accounts.id"), nullable=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id"), nullable=True
    )
    invited_role: Mapped[MembershipRole | None] = mapped_column(
        SAEnum(
            MembershipRole,
            name="one_time_token_invited_role",
            native_enum=False,
            length=30,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=True,
    )
    invitee_email: Mapped[str | None] = mapped_column(String(320))
    inviter_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user_accounts.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        # Deliberately excludes token_hash.
        return f"OneTimeToken(id={self.id!r}, purpose={self.purpose!r})"
