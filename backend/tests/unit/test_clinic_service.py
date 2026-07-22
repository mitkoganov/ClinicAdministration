import uuid
from unittest.mock import patch

import pytest

from app.core.audit import AuditOutcome
from app.core.errors import ForbiddenError
from app.core.tenant_context import TenantContext
from app.models.membership import MembershipRole, MembershipStatus
from app.services.clinic_service import ClinicService

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


def test_owner_can_view_clinic(db_session, tenancy):
    service = ClinicService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    tenant = service.get(context)

    assert tenant.id == tenancy.tenant_a.id


def test_owner_can_update_clinic_name(db_session, tenancy):
    service = ClinicService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    tenant = service.update(context, "Renamed Clinic")

    assert tenant.name == "Renamed Clinic"


def test_manager_cannot_update_clinic_name(db_session, tenancy):
    service = ClinicService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)

    with pytest.raises(ForbiddenError):
        service.update(context, "Hijacked name")


def test_operator_cannot_update_clinic_name(db_session, tenancy):
    service = ClinicService(db_session)
    context = _context(tenancy, tenancy.operator_a, MembershipRole.OPERATOR)

    with pytest.raises(ForbiddenError):
        service.update(context, "Hijacked name")


def test_auditor_can_view_but_not_update(db_session, tenancy):
    service = ClinicService(db_session)
    context = _context(tenancy, tenancy.auditor_a, MembershipRole.AUDITOR)

    tenant = service.get(context)
    assert tenant.id == tenancy.tenant_a.id

    with pytest.raises(ForbiddenError):
        service.update(context, "Hijacked name")


def test_update_cannot_affect_another_tenant(db_session, tenancy):
    service = ClinicService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    service.update(context, "Tenant A Renamed")

    db_session.expire_all()
    untouched = ClinicService(db_session).get(
        _context(tenancy, tenancy.owner_b, MembershipRole.OWNER, tenant=tenancy.tenant_b)
    )
    assert untouched.name == tenancy.tenant_b.name


def test_no_success_audit_event_is_emitted_when_commit_fails(db_session, tenancy):
    service = ClinicService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    with (
        patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")),
        patch("app.services.clinic_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(RuntimeError, match="simulated commit failure"),
    ):
        service.update(context, "Should never be audited as a success")

    success_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.SUCCESS
    ]
    assert success_events == []


def test_rejected_update_is_audited(db_session, tenancy):
    service = ClinicService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)

    with (
        patch("app.services.clinic_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(ForbiddenError),
    ):
        service.update(context, "Hijacked name")

    rejected_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.REJECTED
    ]
    assert len(rejected_events) == 1
    assert rejected_events[0].event_type == "clinic.update"
