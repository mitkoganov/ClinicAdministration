import uuid
from unittest.mock import patch

import pytest

from app.core.audit import AuditOutcome
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.tenant_context import TenantContext
from app.models.membership import MembershipRole, MembershipStatus
from app.services.staff_service import StaffService

# Every test in this module uses db_session/tenancy - a real disposable
# Postgres test database.
pytestmark = pytest.mark.integration


def _context(tenancy, user_id, role, membership_id=None, tenant=None) -> TenantContext:
    tenant = tenant or tenancy.tenant_a
    return TenantContext(
        user_id=user_id,
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        membership_id=membership_id or uuid.uuid4(),
        role=role,
        membership_status=MembershipStatus.ACTIVE,
    )


def _membership_id(db_session, tenancy, user_id, tenant=None) -> uuid.UUID:
    from app.repositories.membership import MembershipRepository

    tenant = tenant or tenancy.tenant_a
    membership = MembershipRepository(db_session).get_membership(tenant.id, user_id)
    assert membership is not None
    return membership.id


def _make_owner_a_the_sole_active_owner(db_session, tenancy) -> None:
    # The base `tenancy` fixture gives tenant_a TWO active owners: owner_a
    # and dual_member (who is separately an owner of tenant_b too). The
    # final-owner tests below need a tenant with exactly one active owner,
    # so demote dual_member's tenant_a membership out of the way first -
    # this itself must succeed, since dual_member is not tenant_a's last
    # owner at this point.
    service = StaffService(db_session)
    owner_context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)
    dual_member_id = _membership_id(db_session, tenancy, tenancy.dual_member)
    service.update(owner_context, dual_member_id, role=MembershipRole.MANAGER)


# --- list -------------------------------------------------------------------


def test_owner_can_list_staff(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    items, total = service.list(context)

    assert total >= 5
    assert all(m.tenant_id == tenancy.tenant_a.id for m in items)


def test_cross_tenant_staff_never_appears(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    items, _ = service.list(context, limit=100)

    assert all(m.tenant_id == tenancy.tenant_a.id for m in items)
    assert tenancy.owner_b not in [m.user_id for m in items]


def test_operator_cannot_list_staff(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.operator_a, MembershipRole.OPERATOR)

    with pytest.raises(ForbiddenError):
        service.list(context)


def test_auditor_can_list_staff(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.auditor_a, MembershipRole.AUDITOR)

    items, total = service.list(context)

    assert total >= 5


def test_list_role_filter_is_tenant_scoped(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    items, total = service.list(context, role=MembershipRole.OWNER)

    # tenant_a has two active owners in the base fixture: owner_a, and
    # dual_member (who is also, separately, an owner of tenant_b).
    assert total == 2
    assert tenancy.owner_a in [m.user_id for m in items]
    assert all(m.tenant_id == tenancy.tenant_a.id for m in items)


def test_list_pagination_is_tenant_scoped(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    page1, total = service.list(context, limit=2, offset=0)
    page2, _ = service.list(context, limit=2, offset=2)

    assert len(page1) == 2
    assert len(page2) == 2
    assert {m.id for m in page1}.isdisjoint({m.id for m in page2})
    assert total >= 5


# --- create -------------------------------------------------------------------


def test_owner_can_add_operator(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)
    new_user = uuid.uuid4()

    membership = service.create(context, new_user, MembershipRole.OPERATOR)

    assert membership.tenant_id == tenancy.tenant_a.id
    assert membership.role == MembershipRole.OPERATOR


def test_owner_can_add_another_owner(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)
    new_user = uuid.uuid4()

    membership = service.create(context, new_user, MembershipRole.OWNER)

    assert membership.role == MembershipRole.OWNER


def test_duplicate_membership_is_rejected(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    with pytest.raises(ConflictError):
        service.create(context, tenancy.manager_a, MembershipRole.OPERATOR)


def test_manager_can_add_operator_and_auditor(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)

    op = service.create(context, uuid.uuid4(), MembershipRole.OPERATOR)
    auditor = service.create(context, uuid.uuid4(), MembershipRole.AUDITOR)

    assert op.role == MembershipRole.OPERATOR
    assert auditor.role == MembershipRole.AUDITOR


def test_manager_cannot_grant_owner(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)

    with pytest.raises(ForbiddenError):
        service.create(context, uuid.uuid4(), MembershipRole.OWNER)


def test_manager_cannot_grant_manager(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)

    with pytest.raises(ForbiddenError):
        service.create(context, uuid.uuid4(), MembershipRole.MANAGER)


def test_manager_cannot_grant_content_editor(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)

    with pytest.raises(ForbiddenError):
        service.create(context, uuid.uuid4(), MembershipRole.CONTENT_EDITOR)


def test_operator_cannot_add_staff(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.operator_a, MembershipRole.OPERATOR)

    with pytest.raises(ForbiddenError):
        service.create(context, uuid.uuid4(), MembershipRole.OPERATOR)


def test_auditor_cannot_add_staff(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.auditor_a, MembershipRole.AUDITOR)

    with pytest.raises(ForbiddenError):
        service.create(context, uuid.uuid4(), MembershipRole.OPERATOR)


# --- update: role changes ----------------------------------------------------


def test_owner_can_change_operator_role(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)
    target_id = _membership_id(db_session, tenancy, tenancy.operator_a)

    updated = service.update(context, target_id, role=MembershipRole.MANAGER)

    assert updated.role == MembershipRole.MANAGER


def test_manager_cannot_mutate_owner(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)
    owner_membership_id = _membership_id(db_session, tenancy, tenancy.owner_a)

    with pytest.raises(ForbiddenError):
        service.update(context, owner_membership_id, role=MembershipRole.MANAGER)


def test_manager_cannot_grant_owner_via_update(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)
    target_id = _membership_id(db_session, tenancy, tenancy.operator_a)

    with pytest.raises(ForbiddenError):
        service.update(context, target_id, role=MembershipRole.OWNER)


def test_manager_can_change_operator_to_auditor(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)
    target_id = _membership_id(db_session, tenancy, tenancy.operator_a)

    updated = service.update(context, target_id, role=MembershipRole.AUDITOR)

    assert updated.role == MembershipRole.AUDITOR


def test_manager_cannot_change_role_of_another_manager(db_session, tenancy):
    service = StaffService(db_session)
    owner_context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)
    other_manager_id = service.create(owner_context, uuid.uuid4(), MembershipRole.MANAGER).id

    manager_context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)
    with pytest.raises(ForbiddenError):
        service.update(manager_context, other_manager_id, role=MembershipRole.OPERATOR)


def test_manager_cannot_change_role_of_content_editor(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)
    target_id = _membership_id(db_session, tenancy, tenancy.content_editor_a)

    with pytest.raises(ForbiddenError):
        service.update(context, target_id, role=MembershipRole.AUDITOR)


def test_manager_cannot_administer_own_manager_membership(db_session, tenancy):
    # A manager's own membership role is MANAGER - not in the manager-
    # administrable target set - so self-targeting must not be a way to
    # bypass that restriction.
    service = StaffService(db_session)
    manager_membership_id = _membership_id(db_session, tenancy, tenancy.manager_a)
    context = _context(
        tenancy, tenancy.manager_a, MembershipRole.MANAGER, membership_id=manager_membership_id
    )

    with pytest.raises(ForbiddenError):
        service.update(context, manager_membership_id, status=MembershipStatus.INACTIVE)


def test_self_elevation_is_rejected(db_session, tenancy):
    service = StaffService(db_session)
    manager_membership_id = _membership_id(db_session, tenancy, tenancy.manager_a)
    context = _context(
        tenancy, tenancy.manager_a, MembershipRole.MANAGER, membership_id=manager_membership_id
    )

    with pytest.raises(ForbiddenError):
        service.update(context, manager_membership_id, role=MembershipRole.OWNER)


def test_self_demotion_is_allowed_when_not_last_owner(db_session, tenancy):
    # dual_member is OWNER in both tenant_a and tenant_b, and tenant_a also
    # has owner_a as an active owner - so demoting dual_member's tenant_a
    # membership leaves tenant_a with an active owner (owner_a).
    service = StaffService(db_session)
    dual_membership_id = _membership_id(db_session, tenancy, tenancy.dual_member)
    context = _context(
        tenancy, tenancy.dual_member, MembershipRole.OWNER, membership_id=dual_membership_id
    )

    updated = service.update(context, dual_membership_id, role=MembershipRole.MANAGER)

    assert updated.role == MembershipRole.MANAGER


def test_operator_cannot_change_roles(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.operator_a, MembershipRole.OPERATOR)
    target_id = _membership_id(db_session, tenancy, tenancy.content_editor_a)

    with pytest.raises(ForbiddenError):
        service.update(context, target_id, role=MembershipRole.AUDITOR)


def test_auditor_cannot_change_roles(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.auditor_a, MembershipRole.AUDITOR)
    target_id = _membership_id(db_session, tenancy, tenancy.content_editor_a)

    with pytest.raises(ForbiddenError):
        service.update(context, target_id, role=MembershipRole.OPERATOR)


def test_cross_tenant_membership_update_returns_not_found(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)
    other_tenant_membership_id = _membership_id(
        db_session, tenancy, tenancy.owner_b, tenant=tenancy.tenant_b
    )

    with pytest.raises(NotFoundError):
        service.update(context, other_tenant_membership_id, role=MembershipRole.MANAGER)


def test_nonexistent_membership_update_returns_not_found(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    with pytest.raises(NotFoundError):
        service.update(context, uuid.uuid4(), role=MembershipRole.MANAGER)


# --- update: active/inactive transitions ------------------------------------


def test_owner_can_deactivate_and_reactivate_operator(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)
    target_id = _membership_id(db_session, tenancy, tenancy.operator_a)

    deactivated = service.update(context, target_id, status=MembershipStatus.INACTIVE)
    assert deactivated.status == MembershipStatus.INACTIVE

    reactivated = service.update(context, target_id, status=MembershipStatus.ACTIVE)
    assert reactivated.status == MembershipStatus.ACTIVE


def test_manager_cannot_deactivate_owner(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)
    owner_membership_id = _membership_id(db_session, tenancy, tenancy.owner_a)

    with pytest.raises(ForbiddenError):
        service.update(context, owner_membership_id, status=MembershipStatus.INACTIVE)


# --- final-owner invariant ----------------------------------------------------


def test_final_owner_cannot_be_demoted(db_session, tenancy):
    _make_owner_a_the_sole_active_owner(db_session, tenancy)
    service = StaffService(db_session)
    owner_membership_id = _membership_id(db_session, tenancy, tenancy.owner_a)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    with pytest.raises(ConflictError):
        service.update(context, owner_membership_id, role=MembershipRole.MANAGER)


def test_final_owner_cannot_be_deactivated(db_session, tenancy):
    _make_owner_a_the_sole_active_owner(db_session, tenancy)
    service = StaffService(db_session)
    owner_membership_id = _membership_id(db_session, tenancy, tenancy.owner_a)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    with pytest.raises(ConflictError):
        service.update(context, owner_membership_id, status=MembershipStatus.INACTIVE)


def test_final_owner_cannot_be_removed(db_session, tenancy):
    _make_owner_a_the_sole_active_owner(db_session, tenancy)
    service = StaffService(db_session)
    owner_membership_id = _membership_id(db_session, tenancy, tenancy.owner_a)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    with pytest.raises(ConflictError):
        service.delete(context, owner_membership_id)


def test_second_owner_can_be_demoted(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)
    second_owner_id = service.create(context, uuid.uuid4(), MembershipRole.OWNER).id

    updated = service.update(context, second_owner_id, role=MembershipRole.MANAGER)

    assert updated.role == MembershipRole.MANAGER


# --- delete (soft removal) ---------------------------------------------------


def test_owner_can_remove_operator(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)
    target_id = _membership_id(db_session, tenancy, tenancy.operator_a)

    service.delete(context, target_id)

    items, _ = service.list(context, limit=100, status=MembershipStatus.INACTIVE)
    assert target_id in [m.id for m in items]


def test_manager_can_remove_operator_and_auditor(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)
    operator_id = _membership_id(db_session, tenancy, tenancy.operator_a)
    auditor_id = _membership_id(db_session, tenancy, tenancy.auditor_a)

    service.delete(context, operator_id)
    service.delete(context, auditor_id)


def test_manager_cannot_remove_owner(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)
    owner_membership_id = _membership_id(db_session, tenancy, tenancy.owner_a)

    with pytest.raises(ForbiddenError):
        service.delete(context, owner_membership_id)


def test_manager_cannot_remove_another_manager(db_session, tenancy):
    service = StaffService(db_session)
    owner_context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)
    other_manager_id = service.create(owner_context, uuid.uuid4(), MembershipRole.MANAGER).id

    manager_context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)
    with pytest.raises(ForbiddenError):
        service.delete(manager_context, other_manager_id)


def test_manager_cannot_remove_content_editor(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.manager_a, MembershipRole.MANAGER)
    content_editor_id = _membership_id(db_session, tenancy, tenancy.content_editor_a)

    with pytest.raises(ForbiddenError):
        service.delete(context, content_editor_id)


def test_manager_cannot_remove_self(db_session, tenancy):
    service = StaffService(db_session)
    manager_membership_id = _membership_id(db_session, tenancy, tenancy.manager_a)
    context = _context(
        tenancy, tenancy.manager_a, MembershipRole.MANAGER, membership_id=manager_membership_id
    )

    with pytest.raises(ForbiddenError):
        service.delete(context, manager_membership_id)


def test_operator_cannot_remove_staff(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.operator_a, MembershipRole.OPERATOR)
    target_id = _membership_id(db_session, tenancy, tenancy.content_editor_a)

    with pytest.raises(ForbiddenError):
        service.delete(context, target_id)


def test_auditor_cannot_remove_staff(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.auditor_a, MembershipRole.AUDITOR)
    target_id = _membership_id(db_session, tenancy, tenancy.content_editor_a)

    with pytest.raises(ForbiddenError):
        service.delete(context, target_id)


def test_cross_tenant_delete_returns_not_found(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)
    other_tenant_membership_id = _membership_id(
        db_session, tenancy, tenancy.owner_b, tenant=tenancy.tenant_b
    )

    with pytest.raises(NotFoundError):
        service.delete(context, other_tenant_membership_id)


# --- audit timing --------------------------------------------------------------


def test_no_success_audit_event_is_emitted_when_commit_fails(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)

    with (
        patch.object(db_session, "commit", side_effect=RuntimeError("simulated commit failure")),
        patch("app.services.staff_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(RuntimeError, match="simulated commit failure"),
    ):
        service.create(context, uuid.uuid4(), MembershipRole.OPERATOR)

    success_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.SUCCESS
    ]
    assert success_events == []


def test_role_change_is_audited_only_after_commit(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.owner_a, MembershipRole.OWNER)
    target_id = _membership_id(db_session, tenancy, tenancy.operator_a)

    with patch("app.services.staff_service.emit_audit_event") as mock_emit_audit_event:
        service.update(context, target_id, role=MembershipRole.MANAGER)

    success_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.SUCCESS
    ]
    assert len(success_events) == 1
    assert success_events[0].event_type == "membership.role_changed"


def test_rejected_create_is_audited(db_session, tenancy):
    service = StaffService(db_session)
    context = _context(tenancy, tenancy.operator_a, MembershipRole.OPERATOR)

    with (
        patch("app.services.staff_service.emit_audit_event") as mock_emit_audit_event,
        pytest.raises(ForbiddenError),
    ):
        service.create(context, uuid.uuid4(), MembershipRole.OPERATOR)

    rejected_events = [
        call.args[0]
        for call in mock_emit_audit_event.call_args_list
        if call.args[0].outcome == AuditOutcome.REJECTED
    ]
    assert len(rejected_events) == 1
    assert rejected_events[0].event_type == "membership.create"
