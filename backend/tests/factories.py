"""Reusable multi-tenant test fixtures required by tasks/current/task.md:
two tenants, users covering every role plus an inactive tenant/membership
and a dual-membership user, and one TenantScopedRecord per tenant."""

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from app.models.membership import MembershipRole, MembershipStatus, TenantMembership
from app.models.tenant import Tenant, TenantStatus
from app.models.tenant_scoped_record import TenantScopedRecord


@dataclass
class Tenancy:
    tenant_a: Tenant
    tenant_b: Tenant
    inactive_tenant: Tenant

    owner_a: uuid.UUID
    manager_a: uuid.UUID
    operator_a: uuid.UUID
    content_editor_a: uuid.UUID
    auditor_a: uuid.UUID
    inactive_member_a: uuid.UUID
    dual_member: uuid.UUID
    stranger: uuid.UUID  # a user with no membership anywhere
    owner_b: uuid.UUID

    record_a: TenantScopedRecord
    record_b: TenantScopedRecord


def _membership(
    db: Session,
    tenant: Tenant,
    user_id: uuid.UUID,
    role: MembershipRole,
    status: MembershipStatus = MembershipStatus.ACTIVE,
) -> TenantMembership:
    membership = TenantMembership(tenant_id=tenant.id, user_id=user_id, role=role, status=status)
    db.add(membership)
    db.flush()
    return membership


@pytest.fixture
def tenancy(db_session: Session) -> Tenancy:
    db = db_session

    tenant_a = Tenant(name="Tenant A", slug="tenant-a", status=TenantStatus.ACTIVE)
    tenant_b = Tenant(name="Tenant B", slug="tenant-b", status=TenantStatus.ACTIVE)
    inactive_tenant = Tenant(
        name="Inactive Tenant", slug="inactive-tenant", status=TenantStatus.INACTIVE
    )
    db.add_all([tenant_a, tenant_b, inactive_tenant])
    db.flush()

    owner_a = uuid.uuid4()
    manager_a = uuid.uuid4()
    operator_a = uuid.uuid4()
    content_editor_a = uuid.uuid4()
    auditor_a = uuid.uuid4()
    inactive_member_a = uuid.uuid4()
    dual_member = uuid.uuid4()
    stranger = uuid.uuid4()
    owner_b = uuid.uuid4()

    _membership(db, tenant_a, owner_a, MembershipRole.OWNER)
    _membership(db, tenant_a, manager_a, MembershipRole.MANAGER)
    _membership(db, tenant_a, operator_a, MembershipRole.OPERATOR)
    _membership(db, tenant_a, content_editor_a, MembershipRole.CONTENT_EDITOR)
    _membership(db, tenant_a, auditor_a, MembershipRole.AUDITOR)
    _membership(db, tenant_a, inactive_member_a, MembershipRole.OPERATOR, MembershipStatus.INACTIVE)
    _membership(db, tenant_a, dual_member, MembershipRole.OWNER)
    _membership(db, tenant_b, dual_member, MembershipRole.OWNER)
    _membership(db, tenant_b, owner_b, MembershipRole.OWNER)

    record_a = TenantScopedRecord(tenant_id=tenant_a.id, name="Record A")
    record_b = TenantScopedRecord(tenant_id=tenant_b.id, name="Record B")
    db.add_all([record_a, record_b])
    db.flush()

    return Tenancy(
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        inactive_tenant=inactive_tenant,
        owner_a=owner_a,
        manager_a=manager_a,
        operator_a=operator_a,
        content_editor_a=content_editor_a,
        auditor_a=auditor_a,
        inactive_member_a=inactive_member_a,
        dual_member=dual_member,
        stranger=stranger,
        owner_b=owner_b,
        record_a=record_a,
        record_b=record_b,
    )
