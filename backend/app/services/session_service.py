"""Session lifecycle: creation, validation, touch, clinic selection, and
revocation - the single place every session-expiry/validation rule lives.
Callers (the API layer, other services) never re-implement any of these
checks themselves.

Fail-closed validation: `validate_session` rejects a missing/unknown/
revoked/expired session, or an inactive account, uniformly via
`UnauthorizedError` - it never distinguishes which condition applied to
the caller."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.audit import AuditEvent, AuditOutcome, emit_audit_event
from app.core.config import Settings
from app.core.errors import InvalidSessionError, NotFoundError, UnauthorizedError
from app.core.session_tokens import generate_token, hash_token
from app.models.auth_session import AuthSession
from app.models.membership import MembershipStatus
from app.models.tenant import TenantStatus
from app.models.user_account import UserAccount, UserAccountStatus
from app.repositories.auth_session import AuthSessionRepository
from app.repositories.membership import MembershipRepository
from app.repositories.tenant import TenantRepository
from app.repositories.user_account import UserAccountRepository

# Avoid a database write on every single request: only refresh
# last_seen_at/idle_expires_at if this much time has passed since the
# previous refresh (task.md: "Избягвай database write на всяка request").
TOUCH_THRESHOLD = timedelta(minutes=5)


def effective_session_expiry(session: AuthSession) -> datetime:
    """The real remaining lifetime of a session - never later than the
    absolute cap, regardless of what `idle_expires_at` happens to say. The
    touch logic below always clamps a *refreshed* idle expiry to the
    absolute cap, but this is still the single place any caller (e.g.
    `GET /auth/me`) should read an outward-facing expiry from, so a
    pre-existing inconsistent row (or an unusually configured idle
    lifetime longer than the absolute one) can never report an expiry the
    session cannot actually reach."""
    return min(session.idle_expires_at, session.absolute_expires_at)


@dataclass(frozen=True)
class CreatedSession:
    session: AuthSession
    raw_token: str
    raw_csrf_token: str


@dataclass(frozen=True)
class ValidatedSession:
    session: AuthSession
    user: UserAccount


class SessionService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._sessions = AuthSessionRepository(db)
        self._users = UserAccountRepository(db)

    def create_session(self, user: UserAccount) -> CreatedSession:
        """Always issues a brand-new token pair - never reuses or extends
        an existing session. Called fresh on every successful login,
        which is what prevents session fixation: an attacker who
        pre-plants a token in a victim's browser before login gains
        nothing, because login never adopts a caller-supplied token.

        Refuses to issue a session for a non-active account, regardless of
        caller. `AuthService.login` already screens this out earlier (with
        a login-specific generic error), but every session-issuing path -
        including invitation acceptance - must be safe to call on its own
        without a session ever escaping for an account that session
        validation would immediately reject anyway."""
        if user.status != UserAccountStatus.ACTIVE:
            raise UnauthorizedError()
        raw_token = generate_token()
        raw_csrf_token = generate_token()
        now = datetime.now(UTC)
        session = self._sessions.create(
            user_id=user.id,
            session_token_hash=hash_token(raw_token),
            csrf_token_hash=hash_token(raw_csrf_token),
            absolute_expires_at=now
            + timedelta(hours=self._settings.session_absolute_lifetime_hours),
            idle_expires_at=now + timedelta(hours=self._settings.session_idle_lifetime_hours),
        )
        return CreatedSession(session=session, raw_token=raw_token, raw_csrf_token=raw_csrf_token)

    def validate_session(self, raw_token: str) -> ValidatedSession:
        """Every rejection below means a session cookie WAS presented but
        turned out unusable - raised as `InvalidSessionError` (not the
        plain `UnauthorizedError` a missing cookie gets) so the caller can
        clear the now-useless cookies (see app.core.session_cookies's
        dedicated exception handler). A transient commit failure during
        the idle-refresh touch below is deliberately NOT included - the
        session itself is still valid, this request just failed to
        record its own activity, and a retry may well succeed without
        forcing a fresh login."""
        session = self._sessions.get_by_token_hash(hash_token(raw_token))
        if session is None:
            raise InvalidSessionError()
        if session.revoked_at is not None:
            raise InvalidSessionError()

        now = datetime.now(UTC)
        if now >= session.absolute_expires_at or now >= session.idle_expires_at:
            raise InvalidSessionError()

        user = self._users.get_by_id(session.user_id)
        if user is None or user.status != UserAccountStatus.ACTIVE:
            raise InvalidSessionError()

        if now - session.last_seen_at >= TOUCH_THRESHOLD:
            # Never refresh idle expiry past the session's own absolute
            # cap - the absolute lifetime is a hard ceiling regardless of
            # activity (see the module docstring), and an unclamped touch
            # could otherwise push idle_expires_at later than
            # absolute_expires_at, which both misrepresents the session's
            # real remaining lifetime to callers of /auth/me and would be
            # a timestamp inversion at rest.
            refreshed_idle_expires_at = min(
                now + timedelta(hours=self._settings.session_idle_lifetime_hours),
                session.absolute_expires_at,
            )
            self._sessions.touch(session, now, refreshed_idle_expires_at)
            # The idle-refresh write must actually reach the database - a
            # bare flush is only visible within this request's own
            # transaction and is discarded (never committed) once the
            # request's DB session closes, silently undoing the idle
            # extension for every subsequent request. Only commit when a
            # touch was actually due, so a plain read request that doesn't
            # need refreshing never pays for an extra round trip. A commit
            # failure here must not silently pretend the session is still
            # valid with a stale expiry - fail closed instead.
            try:
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise UnauthorizedError() from None

        return ValidatedSession(session=session, user=user)

    def revoke(self, session: AuthSession, reason: str) -> None:
        self._sessions.revoke(session, reason, datetime.now(UTC))
        self._db.commit()

    def revoke_all_for_user(
        self,
        user_id: uuid.UUID,
        reason: str,
        *,
        except_session_id: uuid.UUID | None = None,
    ) -> int:
        return self._sessions.revoke_all_for_user(
            user_id, reason, datetime.now(UTC), except_session_id=except_session_id
        )

    def select_clinic(self, session: AuthSession, user_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        """Validates the target tenant and membership are both active
        before storing the selection - identical checks to
        `resolve_tenant_context`, so a client can never select a clinic
        it could not otherwise access. Raises the same generic
        `NotFoundError` for "no such tenant" and "not a member",
        consistent with this codebase's cross-tenant-enumeration
        prevention convention."""
        tenant = TenantRepository(self._db).get_by_id(tenant_id)
        if tenant is None or tenant.status != TenantStatus.ACTIVE:
            self._audit_clinic_selection(session, user_id, tenant_id, AuditOutcome.REJECTED)
            raise NotFoundError()
        membership = MembershipRepository(self._db).get_membership(tenant_id, user_id)
        if membership is None or membership.status != MembershipStatus.ACTIVE:
            self._audit_clinic_selection(session, user_id, tenant_id, AuditOutcome.REJECTED)
            raise NotFoundError()
        self._sessions.select_tenant(session, tenant_id)
        self._db.commit()
        self._audit_clinic_selection(session, user_id, tenant_id, AuditOutcome.SUCCESS)

    def _audit_clinic_selection(
        self,
        session: AuthSession,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        outcome: AuditOutcome,
    ) -> None:
        emit_audit_event(
            AuditEvent(
                event_type="auth.clinic_selected",
                actor_user_id=user_id,
                target_resource_type="auth_session",
                outcome=outcome,
                tenant_id=tenant_id,
                target_resource_id=session.id,
            )
        )
