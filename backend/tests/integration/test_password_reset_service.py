import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.core.errors import InvalidTokenError, UnauthorizedError, WeakPasswordError
from app.core.passwords import verify_password
from app.core.session_tokens import hash_token
from app.models.membership import MembershipRole
from app.repositories.one_time_token import OneTimeTokenRepository
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
