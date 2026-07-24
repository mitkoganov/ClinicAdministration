import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class AuthSession(Base):
    """A server-side session record. The raw session token is NEVER
    persisted here - only `session_token_hash` (see
    app.core.session_tokens.hash_token). Similarly `csrf_token_hash` binds
    a double-submit CSRF token to this exact session server-side, rather
    than trusting a bare cookie/header match alone (see app.core.csrf).

    `selected_tenant_id` is the only place a session carries any
    tenant-related state, and it is never treated as authoritative for
    role/permission purposes - every tenant-scoped request re-resolves the
    membership and role fresh from the database (see
    app.services.tenant_service.resolve_tenant_context). A role is never
    stored here."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_session_token_hash", "session_token_hash", unique=True),
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_absolute_expires_at", "absolute_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user_accounts.id"), nullable=False
    )
    # SHA-256 hex digest (64 chars) of the opaque raw session token - see
    # app.core.session_tokens.hash_token. The raw token is never stored.
    session_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A short, safe machine label (e.g. "logout", "password_change",
    # "password_reset") - never a free-text field that could end up
    # carrying sensitive detail.
    revocation_reason: Mapped[str | None] = mapped_column(String(100))

    def __repr__(self) -> str:
        # Deliberately excludes session_token_hash and csrf_token_hash -
        # even though these are hashes, not raw tokens, there is no reason
        # for them to appear in a repr/debug dump either.
        return f"AuthSession(id={self.id!r}, user_id={self.user_id!r})"
