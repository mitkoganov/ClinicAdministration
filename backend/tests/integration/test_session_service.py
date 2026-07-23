import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.core.audit import AuditOutcome
from app.core.config import Settings
from app.core.errors import NotFoundError, UnauthorizedError
from app.models.membership import MembershipRole
from app.models.tenant import Tenant, TenantStatus
from app.repositories.auth_session import AuthSessionRepository
from app.repositories.membership import MembershipRepository
from app.services.session_service import TOUCH_THRESHOLD, SessionService

# Every test in this module uses db_session/auth_tenancy - a real
# disposable Postgres test database.
pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings(environment="development")


def test_create_session_generates_unique_token_pairs(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    first = service.create_session(auth_tenancy.owner_user)
    second = service.create_session(auth_tenancy.owner_user)

    assert first.raw_token != second.raw_token
    assert first.raw_csrf_token != second.raw_csrf_token
    assert first.session.session_token_hash != second.session.session_token_hash


def test_validate_session_accepts_a_fresh_session(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)

    validated = service.validate_session(created.raw_token)

    assert validated.user.id == auth_tenancy.owner_user.id
    assert validated.session.id == created.session.id


def test_validate_session_rejects_unknown_token(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    with pytest.raises(UnauthorizedError):
        service.validate_session("this-token-was-never-issued")


def test_validate_session_rejects_revoked_session(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)
    service.revoke(created.session, "test")

    with pytest.raises(UnauthorizedError):
        service.validate_session(created.raw_token)


def test_validate_session_rejects_absolute_expired_session(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)
    created.session.absolute_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.flush()

    with pytest.raises(UnauthorizedError):
        service.validate_session(created.raw_token)


def test_validate_session_rejects_idle_expired_session(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)
    created.session.idle_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.flush()

    with pytest.raises(UnauthorizedError):
        service.validate_session(created.raw_token)


def test_validate_session_rejects_inactive_account(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.inactive_account_user)

    with pytest.raises(UnauthorizedError):
        service.validate_session(created.raw_token)


def test_raw_session_token_is_never_persisted(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)

    stored = AuthSessionRepository(db_session).get_by_token_hash(created.session.session_token_hash)
    assert stored is not None
    assert created.raw_token != stored.session_token_hash

    persisted_hashes = (
        db_session.execute(text("SELECT session_token_hash FROM auth_sessions")).scalars().all()
    )
    assert created.raw_token not in persisted_hashes


def test_select_clinic_accepts_active_membership(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)

    service.select_clinic(created.session, auth_tenancy.owner_user.id, auth_tenancy.tenant_a.id)

    assert created.session.selected_tenant_id == auth_tenancy.tenant_a.id


def test_select_clinic_rejects_cross_tenant_membership(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)

    with pytest.raises(NotFoundError):
        service.select_clinic(created.session, auth_tenancy.owner_user.id, auth_tenancy.tenant_b.id)


def test_select_clinic_rejects_inactive_membership(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.inactive_membership_user)

    with pytest.raises(NotFoundError):
        service.select_clinic(
            created.session, auth_tenancy.inactive_membership_user.id, auth_tenancy.tenant_a.id
        )


def test_select_clinic_rejects_inactive_tenant(db_session, auth_tenancy):
    inactive_tenant = Tenant(name="Inactive", slug="inactive-select", status=TenantStatus.INACTIVE)
    db_session.add(inactive_tenant)
    db_session.flush()
    MembershipRepository(db_session).create(
        inactive_tenant.id, auth_tenancy.owner_user.id, MembershipRole.OWNER
    )

    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)

    with pytest.raises(NotFoundError):
        service.select_clinic(created.session, auth_tenancy.owner_user.id, inactive_tenant.id)


def test_select_clinic_rejects_nonexistent_tenant(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)

    with pytest.raises(NotFoundError):
        service.select_clinic(created.session, auth_tenancy.owner_user.id, uuid.uuid4())


def test_validate_session_commits_idle_refresh_when_due(db_session, auth_tenancy):
    """MED-004 repair (finding 2): a bare `flush()` on `touch()` is only
    visible within the request's own transaction and is discarded once the
    request's DB session closes without an explicit commit, silently
    undoing every idle-lifetime extension. `validate_session` must commit
    whenever it actually performs a touch."""
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)
    db_session.commit()
    original_idle_expires_at = created.session.idle_expires_at

    stale_last_seen = datetime.now(UTC) - TOUCH_THRESHOLD - timedelta(minutes=1)
    created.session.last_seen_at = stale_last_seen
    db_session.commit()

    with patch.object(db_session, "commit", wraps=db_session.commit) as commit_spy:
        validated = service.validate_session(created.raw_token)
        assert commit_spy.call_count == 1

    assert validated.session.last_seen_at > stale_last_seen
    assert validated.session.idle_expires_at > original_idle_expires_at


def test_validate_session_does_not_commit_when_touch_not_due(db_session, auth_tenancy):
    """Bounded refresh interval: a session seen moments ago must not incur
    a write (or a commit) on every single request."""
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)
    db_session.commit()

    with patch.object(db_session, "commit", wraps=db_session.commit) as commit_spy:
        service.validate_session(created.raw_token)
        assert commit_spy.call_count == 0


def test_validate_session_fails_closed_when_touch_commit_fails(db_session, auth_tenancy):
    """A failed idle-refresh commit must not leave the caller believing the
    session is still valid with a silently stale expiry - it must roll
    back and reject the request, not swallow the error."""
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)
    db_session.commit()

    stale_last_seen = datetime.now(UTC) - TOUCH_THRESHOLD - timedelta(minutes=1)
    created.session.last_seen_at = stale_last_seen
    db_session.commit()

    with (
        patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")),
        patch.object(db_session, "rollback", wraps=db_session.rollback) as rollback_spy,
        pytest.raises(UnauthorizedError),
    ):
        service.validate_session(created.raw_token)

    assert rollback_spy.call_count == 1


def test_select_clinic_emits_success_audit_after_commit(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)

    with patch("app.services.session_service.emit_audit_event") as mock_emit_audit_event:
        service.select_clinic(created.session, auth_tenancy.owner_user.id, auth_tenancy.tenant_a.id)

    success_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.SUCCESS
    ]
    assert len(success_events) == 1
    assert success_events[0].event_type == "auth.clinic_selected"
    assert success_events[0].tenant_id == auth_tenancy.tenant_a.id
    assert success_events[0].actor_user_id == auth_tenancy.owner_user.id
    assert success_events[0].target_resource_id == created.session.id


def test_select_clinic_emits_rejected_audit_for_cross_tenant_membership(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)

    with (
        patch("app.services.session_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(NotFoundError),
    ):
        service.select_clinic(created.session, auth_tenancy.owner_user.id, auth_tenancy.tenant_b.id)

    rejected_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.REJECTED
    ]
    assert len(rejected_events) == 1
    assert rejected_events[0].event_type == "auth.clinic_selected"


def test_select_clinic_emits_no_success_audit_when_commit_fails(db_session, auth_tenancy):
    service = SessionService(db_session, _settings())
    created = service.create_session(auth_tenancy.owner_user)

    with (
        patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")),
        patch("app.services.session_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(RuntimeError, match="simulated commit failure"),
    ):
        service.select_clinic(created.session, auth_tenancy.owner_user.id, auth_tenancy.tenant_a.id)

    success_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.SUCCESS
    ]
    assert success_events == []
