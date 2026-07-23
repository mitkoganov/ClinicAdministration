"""Password-reset request/confirm business logic.

A reset request never reveals whether the account exists - the same
`request_reset` call is made (and the same neutral outward response
returned by the API layer) whether or not a token actually got created.
The raw token this module returns from `request_reset` exists only for
this module's own tests and any strictly-gated development hook (see
task.md) - the production API route must never return it."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.audit import AuditEvent, AuditOutcome, emit_audit_event
from app.core.config import Settings
from app.core.errors import InvalidTokenError, WeakPasswordError
from app.core.passwords import InvalidPasswordError, hash_password
from app.core.session_tokens import generate_token, hash_token
from app.models.one_time_token import TokenPurpose
from app.models.user_account import UserAccountStatus, normalize_email
from app.repositories.one_time_token import OneTimeTokenRepository
from app.repositories.user_account import UserAccountRepository
from app.services.session_service import SessionService

_RESOURCE_TYPE = "auth"


class PasswordResetService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._users = UserAccountRepository(db)
        self._tokens = OneTimeTokenRepository(db)
        self._sessions = SessionService(db, settings)

    def request_reset(self, email: str) -> str | None:
        normalized = normalize_email(email)
        user = self._users.get_by_normalized_email(normalized)
        if user is None or user.status != UserAccountStatus.ACTIVE:
            # No token, no DB mutation, no error - the caller (API layer)
            # returns the exact same neutral response either way. Still
            # audited, with no identifying actor and no raw email, so a
            # flood of reset requests against unknown/disabled accounts
            # stays visible without letting the audit trail itself leak
            # which case applied.
            self._audit(None, "auth.password_reset_requested", AuditOutcome.REJECTED)
            return None

        raw_token = generate_token()
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=self._settings.password_reset_token_lifetime_minutes)
        try:
            # An older outstanding reset link must stop working the
            # moment a new one is issued - only the most recently
            # requested token should ever be usable, so a stale link
            # leaked or forgotten in an inbox can't be replayed later.
            self._tokens.revoke_all_password_reset_for_user(user.id, now)
            self._tokens.create_password_reset(user.id, hash_token(raw_token), expires_at)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        self._audit(user.id, "auth.password_reset_requested", AuditOutcome.SUCCESS)
        return raw_token

    def confirm_reset(self, raw_token: str, new_password: str) -> None:
        token = self._tokens.get_by_token_hash(hash_token(raw_token), TokenPurpose.PASSWORD_RESET)
        now = datetime.now(UTC)
        if (
            token is None
            or token.consumed_at is not None
            or token.revoked_at is not None
            or token.expires_at <= now
            or token.user_id is None
        ):
            raise InvalidTokenError()

        user = self._users.get_by_id(token.user_id)
        if user is None or user.status != UserAccountStatus.ACTIVE:
            raise InvalidTokenError()

        try:
            new_hash = hash_password(new_password)
        except InvalidPasswordError as exc:
            raise WeakPasswordError(str(exc)) from exc

        try:
            self._users.update_password_hash(user, new_hash, now)
            self._tokens.consume(token, now)
            # Every OTHER outstanding password-reset token for this
            # account must die with this one - consume() above already
            # marked the submitted token consumed, so this call's own
            # `consumed_at IS NULL` filter leaves it alone and only
            # revokes the others. Without this, a second, older reset
            # link could still be used to take the account over again
            # after the user already completed a reset.
            self._tokens.revoke_all_password_reset_for_user(user.id, now)
            self._sessions.revoke_all_for_user(user.id, "password_reset")
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        self._audit(user.id, "auth.password_reset_completed", AuditOutcome.SUCCESS)

    def _audit(self, user_id: uuid.UUID | None, event_type: str, outcome: AuditOutcome) -> None:
        emit_audit_event(
            AuditEvent(
                event_type=event_type,
                actor_user_id=user_id,
                target_resource_type=_RESOURCE_TYPE,
                outcome=outcome,
            )
        )
