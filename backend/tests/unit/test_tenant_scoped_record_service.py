import uuid
from unittest.mock import patch

import pytest

from app.core.audit import AuditOutcome
from app.core.errors import ForbiddenError, NotFoundError
from app.core.tenant_context import TenantContext
from app.models.membership import MembershipRole, MembershipStatus
from app.services.tenant_scoped_record_service import TenantScopedRecordService

# Every test in this module uses db_session/tenancy - a real disposable
# Postgres test database.
pytestmark = pytest.mark.integration


def _context(tenancy, user_id, role, tenant=None) -> TenantContext:
    tenant = tenant or tenancy.tenant_a
    return TenantContext(
        user_id=user_id,
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        membership_id=uuid.uuid4(),
        role=role,
        membership_status=MembershipStatus.ACTIVE,
    )


def test_create_derives_tenant_id_from_context(db_session, tenancy):
    service = TenantScopedRecordService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    record = service.create(context, "New record")

    assert record.tenant_id == tenancy.tenant_a.id


def test_tenant_scoped_get_works(db_session, tenancy):
    service = TenantScopedRecordService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    record = service.get(context, tenancy.record_a.id)

    assert record.id == tenancy.record_a.id


def test_foreign_tenant_get_returns_not_found(db_session, tenancy):
    service = TenantScopedRecordService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    with pytest.raises(NotFoundError):
        service.get(context, tenancy.record_b.id)


def test_tenant_scoped_update_works(db_session, tenancy):
    service = TenantScopedRecordService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    updated = service.update(context, tenancy.record_a.id, "Updated name")

    assert updated.name == "Updated name"


def test_foreign_tenant_update_is_rejected(db_session, tenancy):
    service = TenantScopedRecordService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    with pytest.raises(NotFoundError):
        service.update(context, tenancy.record_b.id, "Hijacked name")


def test_tenant_scoped_delete_works(db_session, tenancy):
    service = TenantScopedRecordService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    service.delete(context, tenancy.record_a.id)

    with pytest.raises(NotFoundError):
        service.get(context, tenancy.record_a.id)


def test_foreign_tenant_delete_is_rejected(db_session, tenancy):
    service = TenantScopedRecordService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    with pytest.raises(NotFoundError):
        service.delete(context, tenancy.record_b.id)


def test_auditor_cannot_create(db_session, tenancy):
    service = TenantScopedRecordService(db_session)
    context = _context(tenancy, tenancy.auditor_a, MembershipRole.AUDITOR)

    with pytest.raises(ForbiddenError):
        service.create(context, "Should not be created")


def test_operator_cannot_delete(db_session, tenancy):
    service = TenantScopedRecordService(db_session)
    context = _context(tenancy, tenancy.operator_a, MembershipRole.OPERATOR)

    with pytest.raises(ForbiddenError):
        service.delete(context, tenancy.record_a.id)


def test_no_success_audit_event_is_emitted_when_commit_fails(db_session, tenancy):
    # The service must commit BEFORE emitting a success audit event, not
    # after - forcing db.commit() to raise proves a "success" audit entry
    # is never recorded for a mutation that didn't actually persist.
    service = TenantScopedRecordService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    with (
        patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")),
        patch(
            "app.services.tenant_scoped_record_service.emit_audit_event"
        ) as mock_emit_audit_event,
        pytest.raises(RuntimeError, match="simulated commit failure"),
    ):
        service.create(context, "Should never be audited as a success")

    success_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.SUCCESS
    ]
    assert success_events == []
