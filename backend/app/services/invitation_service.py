"""Staff-invitation issuance (internal helper - no public "send invitation"
endpoint exists in this foundation slice, since email delivery is out of
scope) and acceptance.

The invitation token's own stored context - tenant, role, invitee email,
inviter - is authoritative. `accept_invitation`'s caller-supplied
arguments are limited to the display name and chosen password; the
client can never choose or influence the tenant or role, matching
task.md's invitation-acceptance requirements exactly.

Anonymous invitation acceptance can only ever create a brand-new
account - it never mutates an existing one, active or inactive (see
`accept_invitation`'s docstring for why). Invitation acceptance and
session issuance are one atomic transaction owned entirely by this
service - the API layer performs no commit of its own."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.audit import AuditEvent, AuditOutcome, emit_audit_event
from app.core.config import Settings
from app.core.errors import InvalidTokenError, WeakPasswordError
from app.core.passwords import InvalidPasswordError, hash_password
from app.core.session_tokens import generate_token, hash_token
from app.models.membership import MembershipRole, TenantMembership
from app.models.one_time_token import OneTimeToken, TokenPurpose
from app.models.user_account import UserAccount, normalize_email
from app.repositories.membership import MembershipRepository
from app.repositories.one_time_token import OneTimeTokenRepository
from app.repositories.user_account import UserAccountRepository
from app.services.session_service import CreatedSession, SessionService


@dataclass(frozen=True)
class InvitationAcceptanceResult:
    user: UserAccount
    membership: TenantMembership
    session: CreatedSession


class InvitationService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._users = UserAccountRepository(db)
        self._tokens = OneTimeTokenRepository(db)
        self._memberships = MembershipRepository(db)

    def create_invitation(
        self,
        tenant_id: uuid.UUID,
        role: MembershipRole,
        invitee_email: str,
        inviter_user_id: uuid.UUID,
    ) -> str:
        """Not exposed via any HTTP route in this slice - called directly
        by whatever staff-management flow decides to invite someone (or,
        today, by tests exercising the acceptance side). Returns the raw
        token; the caller is responsible for delivering it (email
        delivery itself is explicitly out of scope for MED-004)."""
        raw_token = generate_token()
        expires_at = datetime.now(UTC) + timedelta(
            hours=self._settings.invitation_token_lifetime_hours
        )
        self._tokens.create_invitation(
            tenant_id=tenant_id,
            invited_role=role,
            invitee_email=normalize_email(invitee_email),
            inviter_user_id=inviter_user_id,
            token_hash=hash_token(raw_token),
            expires_at=expires_at,
        )
        self._db.commit()
        return raw_token

    def accept_invitation(
        self,
        raw_token: str,
        display_name: str,
        password: str,
        session_service: SessionService,
    ) -> InvitationAcceptanceResult:
        """Anonymous invitation acceptance can only ever create a brand
        new account - never mutate an existing one, regardless of that
        account's status. An `invitee_email` that already belongs to any
        existing account is rejected exactly like an invalid, expired, or
        already-consumed token (`InvalidTokenError`, never a distinct
        error) - it never reveals that the account exists, and it never
        changes that account's password, membership, or session state.
        This closes an account-takeover vector: without this check,
        anyone who obtained a valid invitation link for an email that
        already has an account could overwrite that account's password
        without knowing the current one. Claiming an invitation against
        an account you already own is a separate, authenticated flow this
        foundation slice does not implement yet - not this one.

        Invitation acceptance and session issuance are one atomic
        transaction: the new account, its membership, the new session,
        and token consumption are all flushed together and committed
        exactly once, here - never by two separate commits split across
        this service and the API layer. If anything fails before that
        single commit succeeds (session creation, the flush, the commit
        itself), everything rolls back: the token is never consumed, no
        account or membership is created, and the caller can safely retry
        with the same link."""
        token = self._tokens.get_by_token_hash(
            hash_token(raw_token), TokenPurpose.INVITATION_ACCEPT
        )
        now = datetime.now(UTC)
        if not self._is_usable(token, now):
            raise InvalidTokenError()
        assert token is not None  # narrowed by _is_usable for mypy

        assert token.invitee_email is not None
        assert token.tenant_id is not None
        assert token.invited_role is not None

        existing_user = self._users.get_by_normalized_email(token.invitee_email)
        if existing_user is not None:
            self._audit_invitation_rejected(existing_user, token)
            raise InvalidTokenError()

        try:
            password_hash = hash_password(password)
        except InvalidPasswordError as exc:
            raise WeakPasswordError(str(exc)) from exc

        # Everything from here through the commit is one atomic attempt:
        # any failure - account/membership creation, session issuance, the
        # flush, or the final commit itself - rolls back the whole thing,
        # so a partially-failed attempt never leaves a consumed token
        # without an account, an account without a session, or any other
        # half-completed state. Nothing here is committed except by the
        # single `self._db.commit()` below.
        try:
            user = self._users.create(token.invitee_email, display_name, password_hash)
            membership = self._memberships.create(token.tenant_id, user.id, token.invited_role)
            created_session = session_service.create_session(user)
            self._tokens.consume(token, now)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        self._audit_invitation_accepted(user, token, membership)
        return InvitationAcceptanceResult(user=user, membership=membership, session=created_session)

    @staticmethod
    def _audit_invitation_accepted(
        user: UserAccount, token: OneTimeToken, membership: TenantMembership
    ) -> None:
        emit_audit_event(
            AuditEvent(
                event_type="auth.invitation_accepted",
                actor_user_id=user.id,
                target_resource_type="membership",
                outcome=AuditOutcome.SUCCESS,
                tenant_id=token.tenant_id,
                target_resource_id=membership.id,
            )
        )

    @staticmethod
    def _audit_invitation_rejected(user: UserAccount, token: OneTimeToken) -> None:
        emit_audit_event(
            AuditEvent(
                event_type="auth.invitation_accepted",
                actor_user_id=user.id,
                target_resource_type="membership",
                outcome=AuditOutcome.REJECTED,
                tenant_id=token.tenant_id,
            )
        )

    @staticmethod
    def _is_usable(token: OneTimeToken | None, now: datetime) -> bool:
        return (
            token is not None
            and token.consumed_at is None
            and token.revoked_at is None
            and token.expires_at > now
            and token.tenant_id is not None
            and token.invited_role is not None
            and token.invitee_email is not None
        )
