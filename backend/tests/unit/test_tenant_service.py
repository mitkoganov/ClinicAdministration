import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.errors import NotFoundError
from app.models.membership import MembershipRole, MembershipStatus, TenantMembership
from app.models.tenant import Tenant, TenantStatus
from app.services.tenant_service import normalize_slug, resolve_membership, resolve_tenant

# Every test in this module uses db_session/tenancy - a real disposable
# Postgres test database.
pytestmark = pytest.mark.integration


def test_active_tenant_resolves_successfully(db_session, tenancy):
    tenant = resolve_tenant(db_session, tenancy.tenant_a.id)
    assert tenant.id == tenancy.tenant_a.id


def test_inactive_tenant_is_rejected(db_session, tenancy):
    with pytest.raises(NotFoundError):
        resolve_tenant(db_session, tenancy.inactive_tenant.id)


def test_missing_tenant_is_rejected(db_session, tenancy):
    with pytest.raises(NotFoundError):
        resolve_tenant(db_session, uuid.uuid4())


def test_active_membership_resolves_successfully(db_session, tenancy):
    membership = resolve_membership(db_session, tenancy.tenant_a.id, tenancy.owner_a)
    assert membership.user_id == tenancy.owner_a


def test_missing_membership_is_rejected(db_session, tenancy):
    with pytest.raises(NotFoundError):
        resolve_membership(db_session, tenancy.tenant_a.id, tenancy.stranger)


def test_inactive_membership_is_rejected(db_session, tenancy):
    with pytest.raises(NotFoundError):
        resolve_membership(db_session, tenancy.tenant_a.id, tenancy.inactive_member_a)


def test_role_is_exposed_correctly(db_session, tenancy):
    membership = resolve_membership(db_session, tenancy.tenant_a.id, tenancy.manager_a)
    assert membership.role.value == "manager"


def test_normalized_slug_uniqueness_is_enforced_at_the_database_level(db_session):
    db_session.add(
        Tenant(name="Acme", slug=normalize_slug("Acme Clinic"), status=TenantStatus.ACTIVE)
    )
    db_session.flush()

    db_session.add(
        Tenant(
            name="Acme Duplicate",
            slug=normalize_slug("  ACME   CLINIC "),
            status=TenantStatus.ACTIVE,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_tenant_status_is_persisted_as_the_documented_lowercase_value(db_session):
    # task.md / ARCHITECTURE.md / SECURITY.md document the enum contract in
    # lowercase ("active", "owner", ...) - SQLAlchemy's Enum type persists
    # the Python member NAME ("ACTIVE") unless values_callable forces it to
    # use .value instead. Assert the raw stored string directly, bypassing
    # the ORM's own enum coercion on the way back, so a regression here
    # (e.g. someone removing values_callable) is caught even though
    # round-tripping through the ORM would still "work" either way.
    tenant = Tenant(name="Acme", slug="acme-lowercase-status-check", status=TenantStatus.ACTIVE)
    db_session.add(tenant)
    db_session.flush()

    raw_status = db_session.execute(
        text("SELECT status FROM tenants WHERE id = :id"), {"id": str(tenant.id)}
    ).scalar_one()

    assert raw_status == "active"


def test_membership_role_and_status_are_persisted_as_documented_lowercase_values(
    db_session, tenancy
):
    membership = TenantMembership(
        tenant_id=tenancy.tenant_a.id,
        user_id=uuid.uuid4(),
        role=MembershipRole.CONTENT_EDITOR,
        status=MembershipStatus.ACTIVE,
    )
    db_session.add(membership)
    db_session.flush()

    raw_role, raw_status = db_session.execute(
        text("SELECT role, status FROM tenant_memberships WHERE id = :id"),
        {"id": str(membership.id)},
    ).one()

    assert raw_role == "content_editor"
    assert raw_status == "active"
