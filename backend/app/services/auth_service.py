"""Login, logout, and change-password business logic.

Owns the commit for every mutation it performs, and commits BEFORE
emitting a `SUCCESS` audit event - never after, matching the pattern
established in `app.services.tenant_scoped_record_service` /
`app.services.staff_service`. A failed login never creates a session and
never commits anything."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.audit import AuditEvent, AuditOutcome, emit_audit_event
from app.core.config import Settings
from app.core.errors import RateLimitedError, UnauthorizedError, WeakPasswordError
from app.core.passwords import InvalidPasswordError, hash_password, needs_rehash, verify_password
from app.core.rate_limit import RateLimiter, login_rate_limit_keys
from app.models.auth_session import AuthSession
from app.models.user_account import UserAccount, UserAccountStatus, normalize_email
from app.repositories.user_account import UserAccountRepository
from app.services.session_service import CreatedSession, SessionService

_RESOURCE_TYPE = "auth"

# A precomputed hash used only to keep login timing consistent whether or
# not the submitted email matches a real account - verifying against a
# real Argon2id hash costs roughly the same regardless of outcome, so
# skipping verification entirely for an unknown account would make "no
# such account" measurably faster than "wrong password", leaking account
# existence via a timing side channel.
_DUMMY_PASSWORD_HASH = hash_password("this-is-only-used-for-login-timing-parity")


class AuthService:
    def __init__(self, db: Session, settings: Settings, rate_limiter: RateLimiter | None) -> None:
        self._db = db
        self._settings = settings
        self._users = UserAccountRepository(db)
        self._sessions = SessionService(db, settings)
        self._rate_limiter = rate_limiter

    def login(self, email: str, password: str, client_ip: str | None) -> CreatedSession:
        normalized = normalize_email(email)

        if self._rate_limiter is not None:
            keys = login_rate_limit_keys(normalized, client_ip)
            if not all(self._rate_limiter.check_and_consume(key) for key in keys):
                self._audit(None, "auth.login_rate_limited", AuditOutcome.REJECTED)
                # Never lets rate-limiting itself become a way to
                # distinguish "this account exists" from "it doesn't" -
                # the message/body never varies by account existence.
                raise RateLimitedError()

        user = self._users.get_by_normalized_email(normalized)
        password_ok = verify_password(
            password, user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
        )

        if user is None or user.status != UserAccountStatus.ACTIVE or not password_ok:
            self._audit(user, "auth.login_failure", AuditOutcome.REJECTED)
            raise UnauthorizedError("Invalid email or password.")

        if needs_rehash(user.password_hash):
            # A transparent rehash (stronger Argon2 parameters, discovered
            # incidentally during this successful verification) is
            # maintenance, not a credential change - the user did not
            # change their password, so password_changed_at (and any
            # policy/audit reading it) must stay exactly as it was. See
            # UserAccountRepository.rehash_password_hash.
            self._users.rehash_password_hash(user, hash_password(password))

        if self._rate_limiter is not None:
            for key in login_rate_limit_keys(normalized, client_ip):
                self._rate_limiter.reset(key)

        created = self._sessions.create_session(user)
        self._users.record_successful_login(user, datetime.now(UTC))
        try:
            self._db.commit()
        except Exception:
            # A commit failure here must roll back everything flushed
            # above - the new session, the last-login timestamp, and any
            # transparent rehash - not leave a half-applied state (e.g. a
            # rehashed password_hash with no corresponding session) or a
            # false success audit for a login that never actually
            # completed.
            self._db.rollback()
            raise
        self._audit(user, "auth.login_success", AuditOutcome.SUCCESS)
        return created

    def logout(self, session: AuthSession, user_id: uuid.UUID) -> None:
        self._sessions.revoke(session, "logout")
        self._audit_user_id(user_id, "auth.logout", AuditOutcome.SUCCESS)

    def change_password(
        self,
        user: UserAccount,
        current_session: AuthSession,
        current_password: str,
        new_password: str,
    ) -> None:
        if not verify_password(current_password, user.password_hash):
            self._audit(user, "auth.password_changed", AuditOutcome.REJECTED)
            raise UnauthorizedError("Current password is incorrect.")

        try:
            new_hash = hash_password(new_password)
        except InvalidPasswordError as exc:
            raise WeakPasswordError(str(exc)) from exc

        now = datetime.now(UTC)
        self._users.update_password_hash(user, new_hash, now)
        # The current session is deliberately kept alive (the user just
        # proved their identity with the current password in this same
        # request) - every OTHER session is revoked, so a stolen-but-still
        # -valid session elsewhere is cut off immediately.
        self._sessions.revoke_all_for_user(
            user.id, "password_changed", except_session_id=current_session.id
        )
        self._db.commit()
        self._audit(user, "auth.password_changed", AuditOutcome.SUCCESS)

    def _audit(self, user: UserAccount | None, event_type: str, outcome: AuditOutcome) -> None:
        emit_audit_event(
            AuditEvent(
                event_type=event_type,
                actor_user_id=user.id if user is not None else None,
                target_resource_type=_RESOURCE_TYPE,
                outcome=outcome,
            )
        )

    def _audit_user_id(self, user_id: uuid.UUID, event_type: str, outcome: AuditOutcome) -> None:
        emit_audit_event(
            AuditEvent(
                event_type=event_type,
                actor_user_id=user_id,
                target_resource_type=_RESOURCE_TYPE,
                outcome=outcome,
            )
        )
