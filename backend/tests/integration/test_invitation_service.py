import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.core.audit import AuditOutcome
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


def test_accept_invitation_emits_success_audit_after_commit(db_session, auth_tenancy):
    service = InvitationService(db_session, _settings())
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.OPERATOR,
        f"audited-{uuid.uuid4()}@auth.test",
        auth_tenancy.owner_user.id,
    )

    with patch("app.services.invitation_service.emit_audit_event") as mock_emit_audit_event:
        user = service.accept_invitation(
            raw_token, "Audited Person", "a brand new invitee passphrase!"
        )

    success_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.SUCCESS
    ]
    assert len(success_events) == 1
    event = success_events[0]
    assert event.event_type == "auth.invitation_accepted"
    assert event.actor_user_id == user.id
    assert event.tenant_id == auth_tenancy.tenant_a.id

    membership = MembershipRepository(db_session).get_membership(auth_tenancy.tenant_a.id, user.id)
    assert membership is not None
    assert event.target_resource_id == membership.id

    # The raw token must never appear anywhere in the audit payload.
    audit_dict = event.to_dict()
    assert raw_token not in audit_dict.values()


def test_accept_invitation_emits_no_success_audit_when_commit_fails(db_session, auth_tenancy):
    service = InvitationService(db_session, _settings())
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.OPERATOR,
        f"failed-commit-{uuid.uuid4()}@auth.test",
        auth_tenancy.owner_user.id,
    )

    with (
        patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")),
        patch("app.services.invitation_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(RuntimeError, match="simulated commit failure"),
    ):
        service.accept_invitation(raw_token, "Someone", "a brand new invitee passphrase!")

    success_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.SUCCESS
    ]
    assert success_events == []


def test_accept_invitation_does_not_emit_success_audit_for_a_consumed_token(
    db_session, auth_tenancy
):
    service = InvitationService(db_session, _settings())
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.OPERATOR,
        f"reused-{uuid.uuid4()}@auth.test",
        auth_tenancy.owner_user.id,
    )
    service.accept_invitation(raw_token, "First", "a brand new invitee passphrase!")

    with (
        patch("app.services.invitation_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(InvalidTokenError),
    ):
        service.accept_invitation(raw_token, "Second", "a different passphrase entirely")

    assert mock_emit_audit_event.call_count == 0
