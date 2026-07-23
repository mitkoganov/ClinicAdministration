import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.core.audit import AuditOutcome
from app.core.config import Settings
from app.core.errors import InvalidTokenError
from app.core.passwords import verify_password
from app.core.session_tokens import hash_token
from app.models.membership import MembershipRole, MembershipStatus
from app.models.one_time_token import TokenPurpose
from app.repositories.auth_session import AuthSessionRepository
from app.repositories.membership import MembershipRepository
from app.repositories.one_time_token import OneTimeTokenRepository
from app.repositories.user_account import UserAccountRepository
from app.services.invitation_service import InvitationService
from app.services.session_service import SessionService

# Every test in this module uses db_session/auth_tenancy - a real
# disposable Postgres test database.
pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings(environment="development")


def _service(db_session) -> InvitationService:
    return InvitationService(db_session, _settings())


def _sessions(db_session) -> SessionService:
    return SessionService(db_session, _settings())


def _session_count_for(db_session, user_id) -> int:
    return db_session.execute(
        text("SELECT count(*) FROM auth_sessions WHERE user_id = :user_id"),
        {"user_id": str(user_id)},
    ).scalar_one()


def test_create_and_accept_invitation_creates_a_new_user_and_membership(db_session, auth_tenancy):
    service = _service(db_session)
    invitee_email = f"new-{uuid.uuid4()}@auth.test"
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id, MembershipRole.OPERATOR, invitee_email, auth_tenancy.owner_user.id
    )

    result = service.accept_invitation(
        raw_token, "New Person", "a brand new invitee passphrase!", _sessions(db_session)
    )

    assert result.user.normalized_email == invitee_email.lower()
    assert verify_password("a brand new invitee passphrase!", result.user.password_hash)
    assert result.membership.role == MembershipRole.OPERATOR
    assert result.membership.status == MembershipStatus.ACTIVE
    assert result.session.raw_token
    membership = MembershipRepository(db_session).get_membership(
        auth_tenancy.tenant_a.id, result.user.id
    )
    assert membership is not None
    assert membership.id == result.membership.id


def test_accept_invitation_is_single_use(db_session, auth_tenancy):
    service = _service(db_session)
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.OPERATOR,
        f"solo-{uuid.uuid4()}@auth.test",
        auth_tenancy.owner_user.id,
    )
    service.accept_invitation(
        raw_token, "Someone", "a brand new invitee passphrase!", _sessions(db_session)
    )

    with pytest.raises(InvalidTokenError):
        service.accept_invitation(
            raw_token, "Someone Else", "a different passphrase entirely", _sessions(db_session)
        )


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

    service = _service(db_session)
    with pytest.raises(InvalidTokenError):
        service.accept_invitation(
            "an-expired-invitation-token",
            "Someone",
            "a brand new invitee passphrase!",
            _sessions(db_session),
        )


def test_invitation_token_is_purpose_bound(db_session, auth_tenancy):
    # A password-reset token must never be accepted by the invitation flow.
    reset_tokens = OneTimeTokenRepository(db_session)
    reset_tokens.create_password_reset(
        auth_tenancy.owner_user.id,
        hash_token("a-password-reset-token"),
        datetime.now(UTC) + timedelta(minutes=30),
    )
    db_session.flush()

    service = _service(db_session)
    with pytest.raises(InvalidTokenError):
        service.accept_invitation(
            "a-password-reset-token",
            "Someone",
            "a brand new invitee passphrase!",
            _sessions(db_session),
        )


def test_accept_invitation_emits_success_audit_after_commit(db_session, auth_tenancy):
    service = _service(db_session)
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.OPERATOR,
        f"audited-{uuid.uuid4()}@auth.test",
        auth_tenancy.owner_user.id,
    )

    with patch("app.services.invitation_service.emit_audit_event") as mock_emit_audit_event:
        result = service.accept_invitation(
            raw_token, "Audited Person", "a brand new invitee passphrase!", _sessions(db_session)
        )

    success_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.SUCCESS
    ]
    assert len(success_events) == 1
    event = success_events[0]
    assert event.event_type == "auth.invitation_accepted"
    assert event.actor_user_id == result.user.id
    assert event.tenant_id == auth_tenancy.tenant_a.id
    assert event.target_resource_id == result.membership.id

    # The raw token and raw session token must never appear anywhere in
    # the audit payload.
    audit_dict = event.to_dict()
    assert raw_token not in audit_dict.values()
    assert result.session.raw_token not in audit_dict.values()


def test_accept_invitation_does_not_emit_success_audit_for_a_consumed_token(
    db_session, auth_tenancy
):
    service = _service(db_session)
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.OPERATOR,
        f"reused-{uuid.uuid4()}@auth.test",
        auth_tenancy.owner_user.id,
    )
    service.accept_invitation(
        raw_token, "First", "a brand new invitee passphrase!", _sessions(db_session)
    )

    with (
        patch("app.services.invitation_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(InvalidTokenError),
    ):
        service.accept_invitation(
            raw_token, "Second", "a different passphrase entirely", _sessions(db_session)
        )

    assert mock_emit_audit_event.call_count == 0


def test_accept_invitation_rejects_an_existing_inactive_account(db_session, auth_tenancy):
    """An invitation is never a side channel for implicitly reactivating a
    disabled account - reactivation must be a separate, deliberate
    administrative action. This must fail exactly like any other
    invalid/expired/consumed token (`InvalidTokenError`), never a distinct
    error that would reveal the account's status."""
    service = _service(db_session)
    raw_token = service.create_invitation(
        auth_tenancy.tenant_b.id,
        MembershipRole.OPERATOR,
        auth_tenancy.inactive_account_user.normalized_email,
        auth_tenancy.owner_user.id,
    )

    with pytest.raises(InvalidTokenError):
        service.accept_invitation(
            raw_token, "Should Not Reactivate", "a brand new passphrase!!", _sessions(db_session)
        )


def test_accept_invitation_for_inactive_account_leaves_token_and_membership_untouched(
    db_session, auth_tenancy
):
    service = _service(db_session)
    raw_token = service.create_invitation(
        auth_tenancy.tenant_b.id,
        MembershipRole.OPERATOR,
        auth_tenancy.inactive_account_user.normalized_email,
        auth_tenancy.owner_user.id,
    )

    with pytest.raises(InvalidTokenError):
        service.accept_invitation(
            raw_token, "Should Not Reactivate", "a brand new passphrase!!", _sessions(db_session)
        )

    db_session.expire_all()
    reloaded_token = OneTimeTokenRepository(db_session).get_by_token_hash(
        hash_token(raw_token), TokenPurpose.INVITATION_ACCEPT
    )
    assert reloaded_token is not None
    assert reloaded_token.consumed_at is None

    membership = MembershipRepository(db_session).get_membership(
        auth_tenancy.tenant_b.id, auth_tenancy.inactive_account_user.id
    )
    assert membership is None


def test_accept_invitation_for_inactive_account_emits_only_a_rejected_audit(
    db_session, auth_tenancy
):
    service = _service(db_session)
    raw_token = service.create_invitation(
        auth_tenancy.tenant_b.id,
        MembershipRole.OPERATOR,
        auth_tenancy.inactive_account_user.normalized_email,
        auth_tenancy.owner_user.id,
    )

    with (
        patch("app.services.invitation_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(InvalidTokenError),
    ):
        service.accept_invitation(
            raw_token, "Should Not Reactivate", "a brand new passphrase!!", _sessions(db_session)
        )

    events = [call.args[0] for call in mock_emit_audit_event.call_args_list]
    assert len(events) == 1
    assert events[0].event_type == "auth.invitation_accepted"
    assert events[0].outcome == AuditOutcome.REJECTED
    assert events[0].actor_user_id == auth_tenancy.inactive_account_user.id
    # The raw token must never appear anywhere in the audit payload.
    assert raw_token not in events[0].to_dict().values()


# --- Finding 1 (account-takeover) regression coverage -----------------


def test_accept_invitation_rejects_an_existing_active_account(db_session, auth_tenancy):
    """The core account-takeover regression test: accepting an invitation
    whose invitee_email matches an existing *active* account must never
    succeed, must never change that account's password, and must never
    issue a session - regardless of how the attacker obtained the
    invitation link."""
    service = _service(db_session)
    raw_token = service.create_invitation(
        auth_tenancy.tenant_b.id,
        MembershipRole.OPERATOR,
        auth_tenancy.owner_user.normalized_email,
        auth_tenancy.owner_user.id,
    )
    sessions_before = _session_count_for(db_session, auth_tenancy.owner_user.id)

    with pytest.raises(InvalidTokenError):
        service.accept_invitation(
            raw_token,
            "Attacker Chosen Name",
            "attacker chosen new password!!",
            _sessions(db_session),
        )

    # 1. The old password still works.
    db_session.expire_all()
    reloaded_user = UserAccountRepository(db_session).get_by_id(auth_tenancy.owner_user.id)
    assert reloaded_user is not None
    assert verify_password(auth_tenancy.owner_password, reloaded_user.password_hash)
    # 2. The attacker's chosen password does NOT work.
    assert not verify_password("attacker chosen new password!!", reloaded_user.password_hash)
    # 3. No new session was created for this account.
    assert _session_count_for(db_session, auth_tenancy.owner_user.id) == sessions_before
    # 4. No membership was created in the invitation's target tenant.
    membership = MembershipRepository(db_session).get_membership(
        auth_tenancy.tenant_b.id, auth_tenancy.owner_user.id
    )
    assert membership is None
    # 5. The token remains unconsumed.
    token = OneTimeTokenRepository(db_session).get_by_token_hash(
        hash_token(raw_token), TokenPurpose.INVITATION_ACCEPT
    )
    assert token is not None
    assert token.consumed_at is None


def test_accept_invitation_for_active_account_does_not_reactivate_an_inactive_membership(
    db_session, auth_tenancy
):
    """`inactive_membership_user` has an ACTIVE account but an INACTIVE
    membership in tenant_a. Anonymous invitation acceptance must not use
    this as a side channel to reactivate that membership (or change that
    account's password) either - the active-account rejection applies
    regardless of membership status."""
    service = _service(db_session)
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.AUDITOR,
        auth_tenancy.inactive_membership_user.normalized_email,
        auth_tenancy.owner_user.id,
    )

    with pytest.raises(InvalidTokenError):
        service.accept_invitation(
            raw_token, "Should Not Reactivate", "a brand new passphrase!!", _sessions(db_session)
        )

    db_session.expire_all()
    membership = MembershipRepository(db_session).get_membership(
        auth_tenancy.tenant_a.id, auth_tenancy.inactive_membership_user.id
    )
    assert membership is not None
    assert membership.status == MembershipStatus.INACTIVE

    reloaded_user = UserAccountRepository(db_session).get_by_id(
        auth_tenancy.inactive_membership_user.id
    )
    assert reloaded_user is not None
    assert verify_password(auth_tenancy.inactive_membership_password, reloaded_user.password_hash)


def test_accept_invitation_rejection_for_existing_active_account_emits_only_a_rejected_audit(
    db_session, auth_tenancy
):
    service = _service(db_session)
    raw_token = service.create_invitation(
        auth_tenancy.tenant_b.id,
        MembershipRole.OPERATOR,
        auth_tenancy.owner_user.normalized_email,
        auth_tenancy.owner_user.id,
    )

    with (
        patch("app.services.invitation_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(InvalidTokenError),
    ):
        service.accept_invitation(
            raw_token, "Attacker", "attacker chosen new password!!", _sessions(db_session)
        )

    events = [call.args[0] for call in mock_emit_audit_event.call_args_list]
    assert len(events) == 1
    assert events[0].outcome == AuditOutcome.REJECTED
    success_events = [e for e in events if e.outcome == AuditOutcome.SUCCESS]
    assert success_events == []


# --- Finding 2 (atomicity) coverage ------------------------------------


def test_accept_invitation_commits_membership_and_session_together(db_session, auth_tenancy):
    """Baseline atomicity proof for the success path: by the time
    `accept_invitation` returns, the account, its membership, and its
    session all exist together - none of the three is ever missing."""
    service = _service(db_session)
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.OPERATOR,
        f"atomic-{uuid.uuid4()}@auth.test",
        auth_tenancy.owner_user.id,
    )

    result = service.accept_invitation(
        raw_token, "Atomic Person", "a brand new invitee passphrase!", _sessions(db_session)
    )

    assert UserAccountRepository(db_session).get_by_id(result.user.id) is not None
    assert (
        MembershipRepository(db_session).get_membership(auth_tenancy.tenant_a.id, result.user.id)
        is not None
    )
    assert _session_count_for(db_session, result.user.id) == 1
    token = OneTimeTokenRepository(db_session).get_by_token_hash(
        hash_token(raw_token), TokenPurpose.INVITATION_ACCEPT
    )
    assert token is not None
    assert token.consumed_at is not None


def test_accept_invitation_rolls_back_everything_when_session_creation_fails(
    db_session, auth_tenancy
):
    """A deterministic, injected failure during session-row creation must
    undo the account and membership rows that were already flushed in the
    same attempt - not just leave the token unconsumed. Uses a real
    disposable-Postgres rollback, not a mock standing in for the database
    itself."""
    service = _service(db_session)
    invitee_email = f"rollback-session-{uuid.uuid4()}@auth.test"
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.OPERATOR,
        invitee_email,
        auth_tenancy.owner_user.id,
    )

    with (
        patch.object(
            AuthSessionRepository, "create", side_effect=RuntimeError("simulated session failure")
        ),
        pytest.raises(RuntimeError, match="simulated session failure"),
    ):
        service.accept_invitation(
            raw_token, "Should Roll Back", "a brand new invitee passphrase!!", _sessions(db_session)
        )

    # accept_invitation already rolled back internally on failure - this
    # only re-reads to prove nothing survived, it does not roll back again.
    db_session.expire_all()
    assert UserAccountRepository(db_session).get_by_normalized_email(invitee_email.lower()) is None
    reloaded_token = OneTimeTokenRepository(db_session).get_by_token_hash(
        hash_token(raw_token), TokenPurpose.INVITATION_ACCEPT
    )
    assert reloaded_token is not None
    assert reloaded_token.consumed_at is None


def test_accept_invitation_rolls_back_everything_when_final_commit_fails(db_session, auth_tenancy):
    """Same proof as above, but for a failure at the final commit itself
    rather than an earlier step - the outcome must be identical: nothing
    partially persisted, token still unconsumed and reusable."""
    service = _service(db_session)
    invitee_email = f"rollback-commit-{uuid.uuid4()}@auth.test"
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.OPERATOR,
        invitee_email,
        auth_tenancy.owner_user.id,
    )

    with (
        patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")),
        patch("app.services.invitation_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(RuntimeError, match="simulated commit failure"),
    ):
        service.accept_invitation(
            raw_token, "Should Roll Back", "a brand new invitee passphrase!!", _sessions(db_session)
        )

    assert mock_emit_audit_event.call_count == 0
    db_session.expire_all()
    assert UserAccountRepository(db_session).get_by_normalized_email(invitee_email.lower()) is None
    reloaded_token = OneTimeTokenRepository(db_session).get_by_token_hash(
        hash_token(raw_token), TokenPurpose.INVITATION_ACCEPT
    )
    assert reloaded_token is not None
    assert reloaded_token.consumed_at is None


def test_accept_invitation_can_be_retried_after_a_transient_failure(db_session, auth_tenancy):
    """The whole point of leaving the token unconsumed on failure: a
    caller can retry the exact same link once the transient problem is
    gone, and that retry succeeds normally."""
    service = _service(db_session)
    invitee_email = f"retry-{uuid.uuid4()}@auth.test"
    raw_token = service.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.OPERATOR,
        invitee_email,
        auth_tenancy.owner_user.id,
    )

    with (
        patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")),
        pytest.raises(RuntimeError, match="simulated commit failure"),
    ):
        service.accept_invitation(
            raw_token, "First Attempt", "a brand new invitee passphrase!", _sessions(db_session)
        )

    result = service.accept_invitation(
        raw_token, "Retry Attempt", "a brand new invitee passphrase!", _sessions(db_session)
    )

    assert result.user.normalized_email == invitee_email.lower()
    assert result.membership.status == MembershipStatus.ACTIVE
    assert _session_count_for(db_session, result.user.id) == 1
