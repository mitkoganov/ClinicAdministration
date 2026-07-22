import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.core.errors import InvalidTokenError
from app.core.passwords import verify_password
from app.core.session_tokens import hash_token
from app.models.membership import MembershipRole, MembershipStatus
from app.repositories.membership import MembershipRepository
from app.repositories.one_time_token import OneTimeTokenRepository
from app.services.invitation_service import InvitationService

# Every test in this module uses db_session/auth_tenancy - a real
# disposable Postgres test database.
pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings(environment="development")


def test_create_and_accept_invitation_creates_a_new_user_and_membership(db_session, auth_tenancy):
    service = InvitationService(db_session, _settings())
    invitee_email = f"new-{uuid.uuid4()}@auth.test"
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id, MembershipRole.OPERATOR, invitee_email, auth_tenancy.owner_user.id
    )

    user = service.accept_invitation(raw_token, "New Person", "a brand new invitee passphrase!")

    assert user.normalized_email == invitee_email.lower()
    assert verify_password("a brand new invitee passphrase!", user.password_hash)
    membership = MembershipRepository(db_session).get_membership(auth_tenancy.tenant_a.id, user.id)
    assert membership is not None
    assert membership.role == MembershipRole.OPERATOR
    assert membership.status == MembershipStatus.ACTIVE


def test_accept_invitation_is_single_use(db_session, auth_tenancy):
    service = InvitationService(db_session, _settings())
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.OPERATOR,
        f"solo-{uuid.uuid4()}@auth.test",
        auth_tenancy.owner_user.id,
    )
    service.accept_invitation(raw_token, "Someone", "a brand new invitee passphrase!")

    with pytest.raises(InvalidTokenError):
        service.accept_invitation(raw_token, "Someone Else", "a different passphrase entirely")


def test_accept_invitation_rejects_expired_token(db_session, auth_tenancy):
    tokens = OneTimeTokenRepository(db_session)
    tokens.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.OPERATOR,
        "expired@auth.test",
        auth_tenancy.owner_user.id,
        hash_token("an-expired-invitation-token"),
        datetime.now(UTC) - timedelta(seconds=1),
    )
    db_session.flush()

    service = InvitationService(db_session, _settings())
    with pytest.raises(InvalidTokenError):
        service.accept_invitation(
            "an-expired-invitation-token", "Someone", "a brand new invitee passphrase!"
        )


def test_accept_invitation_reactivates_an_existing_inactive_membership(db_session, auth_tenancy):
    # inactive_membership_user already has an INACTIVE membership in
    # tenant_a - accepting a fresh invitation for the same tenant/user
    # must reactivate it with the invitation's own role, not create a
    # second row (the (tenant_id, user_id) unique constraint would
    # otherwise be violated).
    service = InvitationService(db_session, _settings())
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.AUDITOR,
        auth_tenancy.inactive_membership_user.normalized_email,
        auth_tenancy.owner_user.id,
    )

    service.accept_invitation(raw_token, "Reactivated", "a brand new invitee passphrase!")

    membership = MembershipRepository(db_session).get_membership(
        auth_tenancy.tenant_a.id, auth_tenancy.inactive_membership_user.id
    )
    assert membership is not None
    assert membership.status == MembershipStatus.ACTIVE
    assert membership.role == MembershipRole.AUDITOR


def test_invitation_token_is_purpose_bound(db_session, auth_tenancy):
    # A password-reset token must never be accepted by the invitation flow.
    reset_tokens = OneTimeTokenRepository(db_session)
    reset_tokens.create_password_reset(
        auth_tenancy.owner_user.id,
        hash_token("a-password-reset-token"),
        datetime.now(UTC) + timedelta(minutes=30),
    )
    db_session.flush()

    service = InvitationService(db_session, _settings())
    with pytest.raises(InvalidTokenError):
        service.accept_invitation(
            "a-password-reset-token", "Someone", "a brand new invitee passphrase!"
        )
