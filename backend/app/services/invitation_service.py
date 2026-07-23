"""Staff-invitation issuance (internal helper - no public "send invitation"
endpoint exists in this foundation slice, since email delivery is out of
scope) and acceptance.

The invitation token's own stored context - tenant, role, invitee email,
inviter - is authoritative. `accept_invitation`'s caller-supplied
arguments are limited to the display name and chosen password; the
client can never choose or influence the tenant or role, matching
task.md's invitation-acceptance requirements exactly."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.audit import AuditEvent, AuditOutcome, emit_audit_event
from app.core.config import Settings
from app.core.errors import InvalidTokenError, WeakPasswordError
from app.core.passwords import InvalidPasswordError, hash_password
from app.core.session_tokens import generate_token, hash_token
from app.models.membership import MembershipRole, MembershipStatus, TenantMembership
from app.models.one_time_token import OneTimeToken, TokenPurpose
from app.models.user_account import UserAccount, UserAccountStatus, normalize_email
from app.repositories.membership import MembershipRepository
from app.repositories.one_time_token import OneTimeTokenRepository
from app.repositories.user_account import UserAccountRepository


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

    def accept_invitation(self, raw_token: str, display_name: str, password: str) -> UserAccount:
        token = self._tokens.get_by_token_hash(
            hash_token(raw_token), TokenPurpose.INVITATION_ACCEPT
        )
        now = datetime.now(UTC)
        if not self._is_usable(token, now):
            raise InvalidTokenError()
        assert token is not None  # narrowed by _is_usable for mypy

        try:
            password_hash = hash_password(password)
        except InvalidPasswordError as exc:
            raise WeakPasswordError(str(exc)) from exc

        assert token.invitee_email is not None
        assert token.tenant_id is not None
        assert token.invited_role is not None

        user = self._users.get_by_normalized_email(token.invitee_email)
        if user is None:
            user = self._users.create(token.invitee_email, display_name, password_hash)
        elif user.status != UserAccountStatus.ACTIVE:
            # Deliberate reject policy, not implicit reactivation: an
            # invitation must never be a side channel for reviving a
            # disabled account - that is a separate, deliberate
            # administrative action. Raising before any repository write
            # here means the token is never consumed and the existing
            # membership/account rows are never touched - the whole
            # attempt leaves no trace but a rejected audit event. Reuses
            # InvalidTokenError (not UnauthorizedError) so the response is
            # byte-identical to every other invitation-rejection reason -
            # it never reveals that an account exists and is specifically
            # inactive.
            self._audit_invitation_rejected(user, token)
            raise InvalidTokenError()
        else:
            self._users.update_password_hash(user, password_hash, now)

        existing_membership = self._memberships.get_membership(token.tenant_id, user.id)
        membership: TenantMembership | None
        if existing_membership is None:
            membership = self._memberships.create(token.tenant_id, user.id, token.invited_role)
        else:
            membership = self._memberships.update(
                token.tenant_id,
                existing_membership.id,
                role=token.invited_role,
                status=MembershipStatus.ACTIVE,
            )

        self._tokens.consume(token, now)
        self._db.commit()
        self._audit_invitation_accepted(user, token, membership)
        return user

    @staticmethod
    def _audit_invitation_accepted(
        user: UserAccount, token: OneTimeToken, membership: TenantMembership | None
    ) -> None:
        emit_audit_event(
            AuditEvent(
                event_type="auth.invitation_accepted",
                actor_user_id=user.id,
                target_resource_type="membership",
                outcome=AuditOutcome.SUCCESS,
                tenant_id=token.tenant_id,
                target_resource_id=membership.id if membership is not None else None,
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
