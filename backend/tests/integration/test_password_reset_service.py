import logging
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.core.audit import AuditOutcome
from app.core.config import Settings
from app.core.errors import InvalidTokenError, UnauthorizedError, WeakPasswordError
from app.core.passwords import verify_password
from app.core.session_tokens import hash_token
from app.models.membership import MembershipRole
from app.models.one_time_token import TokenPurpose
from app.repositories.one_time_token import OneTimeTokenRepository
from app.services.invitation_service import InvitationService
from app.services.password_reset_service import PasswordResetService
from app.services.session_service import SessionService

# Every test in this module uses db_session/auth_tenancy - a real
# disposable Postgres test database.
pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings(environment="development")


def test_request_reset_for_unknown_account_returns_none(db_session, auth_tenancy):
    service = PasswordResetService(db_session, _settings())
    assert service.request_reset(f"nonexistent-{uuid.uuid4()}@auth.test") is None


def test_request_reset_for_inactive_account_returns_none(db_session, auth_tenancy):
    service = PasswordResetService(db_session, _settings())
    assert service.request_reset(auth_tenancy.inactive_account_user.normalized_email) is None


def test_request_reset_for_known_account_returns_a_token(db_session, auth_tenancy):
    service = PasswordResetService(db_session, _settings())
    token = service.request_reset(auth_tenancy.owner_user.normalized_email)
    assert token is not None
    assert len(token) >= 32


def test_reset_token_is_stored_only_as_a_hash(db_session, auth_tenancy):
    service = PasswordResetService(db_session, _settings())
    raw_token = service.request_reset(auth_tenancy.owner_user.normalized_email)
    assert raw_token is not None

    persisted_hashes = (
        db_session.execute(text("SELECT token_hash FROM one_time_tokens")).scalars().all()
    )
    assert raw_token not in persisted_hashes
    assert hash_token(raw_token) in persisted_hashes


def test_confirm_reset_updates_password(db_session, auth_tenancy):
    service = PasswordResetService(db_session, _settings())
    raw_token = service.request_reset(auth_tenancy.owner_user.normalized_email)
    new_password = "a brand new reset passphrase!!"

    service.confirm_reset(raw_token, new_password)

    db_session.refresh(auth_tenancy.owner_user)
    assert verify_password(new_password, auth_tenancy.owner_user.password_hash)


def test_confirm_reset_revokes_existing_sessions(db_session, auth_tenancy):
    session_service = SessionService(db_session, _settings())
    created = session_service.create_session(auth_tenancy.owner_user)

    service = PasswordResetService(db_session, _settings())
    raw_token = service.request_reset(auth_tenancy.owner_user.normalized_email)
    service.confirm_reset(raw_token, "a brand new reset passphrase!!")

    with pytest.raises(UnauthorizedError):
        session_service.validate_session(created.raw_token)


def test_confirm_reset_rejects_weak_new_password(db_session, auth_tenancy):
    service = PasswordResetService(db_session, _settings())
    raw_token = service.request_reset(auth_tenancy.owner_user.normalized_email)

    with pytest.raises(WeakPasswordError):
        service.confirm_reset(raw_token, "short")


def test_confirm_reset_is_single_use(db_session, auth_tenancy):
    service = PasswordResetService(db_session, _settings())
    raw_token = service.request_reset(auth_tenancy.owner_user.normalized_email)
    service.confirm_reset(raw_token, "a brand new reset passphrase!!")

    with pytest.raises(InvalidTokenError):
        service.confirm_reset(raw_token, "yet another new passphrase!!")


def test_confirm_reset_rejects_expired_token(db_session, auth_tenancy):
    tokens = OneTimeTokenRepository(db_session)
    expired = tokens.create_password_reset(
        auth_tenancy.owner_user.id,
        hash_token("a-raw-token-value"),
        datetime.now(UTC) - timedelta(seconds=1),
    )
    db_session.flush()
    assert expired.expires_at < datetime.now(UTC)

    service = PasswordResetService(db_session, _settings())
    with pytest.raises(InvalidTokenError):
        service.confirm_reset("a-raw-token-value", "a brand new reset passphrase!!")


def test_confirm_reset_rejects_unknown_token(db_session, auth_tenancy):
    service = PasswordResetService(db_session, _settings())
    with pytest.raises(InvalidTokenError):
        service.confirm_reset("this-token-was-never-issued", "a brand new reset passphrase!!")


def test_confirm_reset_does_not_match_an_invitation_token(db_session, auth_tenancy):
    tokens = OneTimeTokenRepository(db_session)
    invitation_token_hash = hash_token("some-invitation-raw-token")
    tokens.create_invitation(
        auth_tenancy.tenant_a.id,
        MembershipRole.OPERATOR,
        "invitee@auth.test",
        auth_tenancy.owner_user.id,
        invitation_token_hash,
        datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.flush()

    service = PasswordResetService(db_session, _settings())
    with pytest.raises(InvalidTokenError):
        service.confirm_reset("some-invitation-raw-token", "a brand new reset passphrase!!")


# --- MED-004 repair: outstanding password-reset token invalidation -----


def test_confirm_reset_invalidates_other_outstanding_reset_tokens_for_same_user(
    db_session, auth_tenancy
):
    """The core regression test: with two independently-issued reset
    tokens outstanding for one account (bypassing request_reset's own
    revoke-on-issue behavior, to isolate confirm_reset's own
    invalidation), completing a reset with one must invalidate the
    other - an older or leaked link must never survive a completed
    reset."""
    tokens = OneTimeTokenRepository(db_session)
    first_hash = hash_token("first-reset-token")
    second_hash = hash_token("second-reset-token")
    tokens.create_password_reset(
        auth_tenancy.owner_user.id, first_hash, datetime.now(UTC) + timedelta(minutes=30)
    )
    tokens.create_password_reset(
        auth_tenancy.owner_user.id, second_hash, datetime.now(UTC) + timedelta(minutes=30)
    )
    db_session.flush()

    service = PasswordResetService(db_session, _settings())
    service.confirm_reset("first-reset-token", "a brand new reset passphrase!!")

    # The old password no longer works, the new one does.
    db_session.refresh(auth_tenancy.owner_user)
    assert verify_password("a brand new reset passphrase!!", auth_tenancy.owner_user.password_hash)
    assert not verify_password(auth_tenancy.owner_password, auth_tenancy.owner_user.password_hash)

    # The second, never-used token can no longer be used for a further reset.
    with pytest.raises(InvalidTokenError):
        service.confirm_reset("second-reset-token", "yet another passphrase entirely!!")


def test_confirm_reset_leaves_the_submitted_token_only_consumed_not_revoked(
    db_session, auth_tenancy
):
    service = PasswordResetService(db_session, _settings())
    raw_token = service.request_reset(auth_tenancy.owner_user.normalized_email)
    assert raw_token is not None

    service.confirm_reset(raw_token, "a brand new reset passphrase!!")

    db_session.expire_all()
    token = OneTimeTokenRepository(db_session).get_by_token_hash(
        hash_token(raw_token), TokenPurpose.PASSWORD_RESET
    )
    assert token is not None
    assert token.consumed_at is not None
    assert token.revoked_at is None


def test_confirm_reset_does_not_invalidate_an_invitation_token_for_the_same_user(
    db_session, auth_tenancy
):
    invitation_service = InvitationService(db_session, _settings())
    invitee_email = f"invited-{uuid.uuid4()}@auth.test"
    invitation_raw_token = invitation_service.create_invitation(
        auth_tenancy.tenant_b.id,
        MembershipRole.OPERATOR,
        invitee_email,
        auth_tenancy.owner_user.id,
    )

    reset_service = PasswordResetService(db_session, _settings())
    reset_raw_token = reset_service.request_reset(auth_tenancy.owner_user.normalized_email)
    assert reset_raw_token is not None
    reset_service.confirm_reset(reset_raw_token, "a brand new reset passphrase!!")

    # The unrelated invitation token (a different purpose, a different
    # invitee) must still be fully usable afterward.
    result = invitation_service.accept_invitation(
        invitation_raw_token,
        "Still Invited",
        "a brand new invitee passphrase!",
        SessionService(db_session, _settings()),
    )
    assert result.user.normalized_email == invitee_email.lower()


def test_confirm_reset_does_not_invalidate_another_users_reset_token(db_session, auth_tenancy):
    service = PasswordResetService(db_session, _settings())
    owner_raw_token = service.request_reset(auth_tenancy.owner_user.normalized_email)
    other_raw_token = service.request_reset(auth_tenancy.dual_clinic_user.normalized_email)
    assert owner_raw_token is not None
    assert other_raw_token is not None

    service.confirm_reset(owner_raw_token, "a brand new reset passphrase!!")

    # dual_clinic_user's own still-outstanding token must remain usable.
    service.confirm_reset(other_raw_token, "a different brand new passphrase!!")
    db_session.refresh(auth_tenancy.dual_clinic_user)
    assert verify_password(
        "a different brand new passphrase!!", auth_tenancy.dual_clinic_user.password_hash
    )


def test_request_reset_revokes_an_older_outstanding_token_when_issuing_a_new_one(
    db_session, auth_tenancy
):
    service = PasswordResetService(db_session, _settings())
    older_token = service.request_reset(auth_tenancy.owner_user.normalized_email)
    newer_token = service.request_reset(auth_tenancy.owner_user.normalized_email)
    assert older_token is not None
    assert newer_token is not None
    assert older_token != newer_token

    with pytest.raises(InvalidTokenError):
        service.confirm_reset(older_token, "a brand new reset passphrase!!")

    # Only the most recently issued token is usable.
    service.confirm_reset(newer_token, "a brand new reset passphrase!!")
    db_session.refresh(auth_tenancy.owner_user)
    assert verify_password("a brand new reset passphrase!!", auth_tenancy.owner_user.password_hash)


def test_confirm_reset_emits_success_audit_only_after_commit(db_session, auth_tenancy):
    service = PasswordResetService(db_session, _settings())
    raw_token = service.request_reset(auth_tenancy.owner_user.normalized_email)
    assert raw_token is not None

    with patch("app.services.password_reset_service.emit_audit_event") as mock_emit_audit_event:
        service.confirm_reset(raw_token, "a brand new reset passphrase!!")

    success_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.SUCCESS
    ]
    assert len(success_events) == 1
    assert success_events[0].event_type == "auth.password_reset_completed"
    assert raw_token not in success_events[0].to_dict().values()


def test_confirm_reset_rolls_back_everything_when_commit_fails(db_session, auth_tenancy):
    service = PasswordResetService(db_session, _settings())
    raw_token = service.request_reset(auth_tenancy.owner_user.normalized_email)
    assert raw_token is not None
    original_password_hash = auth_tenancy.owner_user.password_hash

    with (
        patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")),
        patch("app.services.password_reset_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(RuntimeError, match="simulated commit failure"),
    ):
        service.confirm_reset(raw_token, "a brand new reset passphrase!!")

    assert mock_emit_audit_event.call_count == 0
    db_session.expire_all()
    reloaded_user = auth_tenancy.owner_user
    assert reloaded_user.password_hash == original_password_hash
    reloaded_token = OneTimeTokenRepository(db_session).get_by_token_hash(
        hash_token(raw_token), TokenPurpose.PASSWORD_RESET
    )
    assert reloaded_token is not None
    assert reloaded_token.consumed_at is None
    assert reloaded_token.revoked_at is None

    # The token was never actually consumed, so it can still be retried
    # successfully once the transient failure is gone.
    service.confirm_reset(raw_token, "a brand new reset passphrase!!")
    db_session.refresh(auth_tenancy.owner_user)
    assert verify_password("a brand new reset passphrase!!", auth_tenancy.owner_user.password_hash)


def test_request_reset_rolls_back_when_commit_fails(db_session, auth_tenancy):
    with (
        patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")),
        patch("app.services.password_reset_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(RuntimeError, match="simulated commit failure"),
    ):
        PasswordResetService(db_session, _settings()).request_reset(
            auth_tenancy.owner_user.normalized_email
        )

    assert mock_emit_audit_event.call_count == 0
    db_session.expire_all()
    persisted_count = db_session.execute(
        text(
            "SELECT count(*) FROM one_time_tokens WHERE user_id = :user_id "
            "AND purpose = 'password_reset'"
        ),
        {"user_id": str(auth_tenancy.owner_user.id)},
    ).scalar_one()
    assert persisted_count == 0


def test_password_reset_never_logs_the_raw_token(db_session, auth_tenancy, caplog):
    service = PasswordResetService(db_session, _settings())
    with caplog.at_level(logging.DEBUG):
        raw_token = service.request_reset(auth_tenancy.owner_user.normalized_email)
        assert raw_token is not None
        service.confirm_reset(raw_token, "a brand new reset passphrase!!")

    for record in caplog.records:
        assert raw_token not in record.getMessage()


def test_confirm_reset_error_is_identical_for_revoked_consumed_and_unknown_tokens(
    db_session, auth_tenancy
):
    """Revoked-by-a-newer-token, already-consumed, and never-issued must
    all be indistinguishable to the caller - the same exception type and
    the same message, never a hint about which condition applied."""
    service = PasswordResetService(db_session, _settings())

    older_token = service.request_reset(auth_tenancy.owner_user.normalized_email)
    newer_token = service.request_reset(auth_tenancy.owner_user.normalized_email)
    assert older_token is not None
    assert newer_token is not None
    service.confirm_reset(newer_token, "a brand new reset passphrase!!")

    with pytest.raises(InvalidTokenError) as revoked_exc_info:
        service.confirm_reset(older_token, "some other passphrase entirely!!")
    with pytest.raises(InvalidTokenError) as consumed_exc_info:
        service.confirm_reset(newer_token, "some other passphrase entirely!!")
    with pytest.raises(InvalidTokenError) as unknown_exc_info:
        service.confirm_reset("this-token-was-never-issued", "some other passphrase entirely!!")

    assert (
        str(revoked_exc_info.value) == str(consumed_exc_info.value) == str(unknown_exc_info.value)
    )
