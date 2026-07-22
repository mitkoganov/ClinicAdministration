import uuid
from unittest.mock import patch

import pytest

from app.core.audit import AuditOutcome
from app.core.config import Settings
from app.core.errors import UnauthorizedError, WeakPasswordError
from app.core.passwords import verify_password
from app.core.rate_limit import RateLimiter
from app.services.auth_service import AuthService

# Every test in this module uses db_session/auth_tenancy - a real
# disposable Postgres test database.
pytestmark = pytest.mark.integration


class _FakeStore:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    def expire(self, key: str, seconds: int) -> None:
        pass

    def ttl(self, key: str) -> int:
        return -1

    def delete(self, key: str) -> None:
        self._counts.pop(key, None)


def _settings() -> Settings:
    return Settings(environment="development")


def _service(db_session, max_attempts: int = 1000) -> AuthService:
    limiter = RateLimiter(_FakeStore(), max_attempts=max_attempts, window_seconds=900)
    return AuthService(db_session, _settings(), limiter)


def test_login_success_creates_session_and_records_last_login(db_session, auth_tenancy):
    service = _service(db_session)
    created = service.login(
        auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password, "203.0.113.1"
    )

    assert created.session.user_id == auth_tenancy.owner_user.id
    db_session.refresh(auth_tenancy.owner_user)
    assert auth_tenancy.owner_user.last_successful_login_at is not None


def test_login_invalid_password_is_generic(db_session, auth_tenancy):
    service = _service(db_session)
    with pytest.raises(UnauthorizedError) as exc_info:
        service.login(
            auth_tenancy.owner_user.normalized_email, "the wrong passphrase entirely", None
        )
    assert "invalid" in str(exc_info.value).lower()


def test_login_nonexistent_account_matches_invalid_password_response(db_session, auth_tenancy):
    service = _service(db_session)
    real_error = None
    fake_error = None
    try:
        service.login(
            auth_tenancy.owner_user.normalized_email, "the wrong passphrase entirely", None
        )
    except UnauthorizedError as exc:
        real_error = (exc.status_code, exc.message)
    try:
        service.login(f"nonexistent-{uuid.uuid4()}@auth.test", "whatever passphrase here", None)
    except UnauthorizedError as exc:
        fake_error = (exc.status_code, exc.message)

    assert real_error == fake_error


def test_login_inactive_account_matches_invalid_password_response(db_session, auth_tenancy):
    service = _service(db_session)
    with pytest.raises(UnauthorizedError) as exc_info:
        service.login(
            auth_tenancy.inactive_account_user.normalized_email,
            auth_tenancy.inactive_account_password,
            None,
        )
    assert "invalid" in str(exc_info.value).lower()


def test_login_password_hash_is_never_exposed_by_the_exception(db_session, auth_tenancy):
    service = _service(db_session)
    with pytest.raises(UnauthorizedError) as exc_info:
        service.login(auth_tenancy.owner_user.normalized_email, "wrong password here", None)
    assert auth_tenancy.owner_user.password_hash not in str(exc_info.value)


def test_no_success_audit_event_is_emitted_when_commit_fails(db_session, auth_tenancy):
    service = _service(db_session)
    with (
        patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")),
        patch("app.services.auth_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(RuntimeError, match="simulated commit failure"),
    ):
        service.login(auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password, None)

    success_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.SUCCESS
    ]
    assert success_events == []


def test_login_success_is_audited_only_after_commit(db_session, auth_tenancy):
    service = _service(db_session)
    with patch("app.services.auth_service.emit_audit_event") as mock_emit_audit_event:
        service.login(auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password, None)

    success_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.SUCCESS
    ]
    assert len(success_events) == 1
    assert success_events[0].event_type == "auth.login_success"


def test_change_password_requires_current_password(db_session, auth_tenancy):
    service = _service(db_session)
    created = service.login(
        auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password, None
    )

    with pytest.raises(UnauthorizedError):
        service.change_password(
            auth_tenancy.owner_user,
            created.session,
            "the wrong current password!!",
            "a brand new passphrase!!",
        )


def test_change_password_rejects_weak_new_password(db_session, auth_tenancy):
    service = _service(db_session)
    created = service.login(
        auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password, None
    )

    with pytest.raises(WeakPasswordError):
        service.change_password(
            auth_tenancy.owner_user, created.session, auth_tenancy.owner_password, "short"
        )


def test_change_password_updates_hash_and_keeps_current_session(db_session, auth_tenancy):
    service = _service(db_session)
    created = service.login(
        auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password, None
    )
    new_password = "a brand new passphrase for the win"

    service.change_password(
        auth_tenancy.owner_user, created.session, auth_tenancy.owner_password, new_password
    )

    assert verify_password(new_password, auth_tenancy.owner_user.password_hash)
    revalidated = service._sessions.validate_session(created.raw_token)
    assert revalidated.session.id == created.session.id


def test_change_password_revokes_other_sessions(db_session, auth_tenancy):
    service = _service(db_session)
    session_1 = service.login(
        auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password, None
    )
    session_2 = service.login(
        auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password, None
    )

    service.change_password(
        auth_tenancy.owner_user,
        session_1.session,
        auth_tenancy.owner_password,
        "yet another new passphrase!",
    )

    with pytest.raises(UnauthorizedError):
        service._sessions.validate_session(session_2.raw_token)
    # session_1 (the one used to authorize the change) stays valid.
    service._sessions.validate_session(session_1.raw_token)


def test_logout_revokes_the_session(db_session, auth_tenancy):
    service = _service(db_session)
    created = service.login(
        auth_tenancy.owner_user.normalized_email, auth_tenancy.owner_password, None
    )

    service.logout(created.session, auth_tenancy.owner_user.id)

    with pytest.raises(UnauthorizedError):
        service._sessions.validate_session(created.raw_token)
