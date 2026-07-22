import uuid

import pytest

from app.core.authorization import (
    CLINIC_WRITE_ROLES,
    DELETE_ROLES,
    READ_ROLES,
    STAFF_MANAGE_ROLES,
    STAFF_READ_ROLES,
    WRITE_ROLES,
    require_role,
)
from app.core.errors import ForbiddenError
from app.core.tenant_context import TenantContext
from app.models.membership import MembershipRole, MembershipStatus


def _context(role: MembershipRole) -> TenantContext:
    return TenantContext(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        tenant_name="Tenant",
        membership_id=uuid.uuid4(),
        role=role,
        membership_status=MembershipStatus.ACTIVE,
    )


def test_allowed_role_succeeds():
    require_role(_context(MembershipRole.OPERATOR), WRITE_ROLES)


def test_disallowed_role_is_rejected():
    with pytest.raises(ForbiddenError):
        require_role(_context(MembershipRole.AUDITOR), WRITE_ROLES)


def test_auditor_cannot_mutate():
    with pytest.raises(ForbiddenError):
        require_role(_context(MembershipRole.AUDITOR), WRITE_ROLES)
    with pytest.raises(ForbiddenError):
        require_role(_context(MembershipRole.AUDITOR), DELETE_ROLES)


def test_auditor_can_read():
    require_role(_context(MembershipRole.AUDITOR), READ_ROLES)


def test_owner_can_delete():
    require_role(_context(MembershipRole.OWNER), DELETE_ROLES)


def test_manager_can_delete():
    require_role(_context(MembershipRole.MANAGER), DELETE_ROLES)


def test_operator_cannot_delete():
    with pytest.raises(ForbiddenError):
        require_role(_context(MembershipRole.OPERATOR), DELETE_ROLES)


def test_missing_context_is_rejected():
    with pytest.raises(ForbiddenError):
        require_role(None, WRITE_ROLES)


def test_only_owner_can_write_clinic_settings():
    require_role(_context(MembershipRole.OWNER), CLINIC_WRITE_ROLES)
    for role in (
        MembershipRole.MANAGER,
        MembershipRole.OPERATOR,
        MembershipRole.CONTENT_EDITOR,
        MembershipRole.AUDITOR,
    ):
        with pytest.raises(ForbiddenError):
            require_role(_context(role), CLINIC_WRITE_ROLES)


def test_staff_read_roles_exclude_operator_and_content_editor():
    for role in (MembershipRole.OWNER, MembershipRole.MANAGER, MembershipRole.AUDITOR):
        require_role(_context(role), STAFF_READ_ROLES)
    for role in (MembershipRole.OPERATOR, MembershipRole.CONTENT_EDITOR):
        with pytest.raises(ForbiddenError):
            require_role(_context(role), STAFF_READ_ROLES)


def test_staff_manage_roles_are_owner_and_manager_only():
    require_role(_context(MembershipRole.OWNER), STAFF_MANAGE_ROLES)
    require_role(_context(MembershipRole.MANAGER), STAFF_MANAGE_ROLES)
    for role in (MembershipRole.OPERATOR, MembershipRole.CONTENT_EDITOR, MembershipRole.AUDITOR):
        with pytest.raises(ForbiddenError):
            require_role(_context(role), STAFF_MANAGE_ROLES)


def test_missing_context_does_not_raise_attribute_error():
    # A regression guard for the exact failure mode reported: require_role
    # must never dereference `context.role` before checking `context` is
    # present - a bare AttributeError here would surface as an uncontrolled
    # 500 instead of a controlled authorization failure.
    try:
        require_role(None, WRITE_ROLES)
    except ForbiddenError:
        pass
    except AttributeError:
        pytest.fail("require_role(None, ...) raised AttributeError instead of failing closed")
