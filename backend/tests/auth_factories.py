"""Reusable authentication test fixtures: real `UserAccount` rows (with
known plaintext passwords, for login tests) wired to the same tenant/
membership model `tests.factories.tenancy` uses - two clinics, a user
with memberships in both, a user with no membership anywhere, an inactive
account, and a user whose only membership is inactive."""

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from app.core.passwords import hash_password
from app.models.membership import MembershipRole, MembershipStatus, TenantMembership
from app.models.tenant import Tenant, TenantStatus
from app.models.user_account import UserAccount, UserAccountStatus


@dataclass
class AuthTenancy:
    tenant_a: Tenant
    tenant_b: Tenant

    owner_user: UserAccount
    owner_password: str

    dual_clinic_user: UserAccount
    dual_clinic_password: str

    no_membership_user: UserAccount
    no_membership_password: str

    inactive_account_user: UserAccount
    inactive_account_password: str

    inactive_membership_user: UserAccount
    inactive_membership_password: str


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


def _make_user(
    db: Session, email: str, password: str, status: UserAccountStatus = UserAccountStatus.ACTIVE
) -> UserAccount:
    user = UserAccount(
        normalized_email=email,
        display_name=email.split("@")[0].title(),
        password_hash=hash_password(password),
        status=status,
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def auth_tenancy(db_session: Session) -> AuthTenancy:
    db = db_session

    tenant_a = Tenant(name="Auth Tenant A", slug="auth-tenant-a", status=TenantStatus.ACTIVE)
    tenant_b = Tenant(name="Auth Tenant B", slug="auth-tenant-b", status=TenantStatus.ACTIVE)
    db.add_all([tenant_a, tenant_b])
    db.flush()

    owner_password = "correct horse battery staple"
    owner_user = _make_user(db, "owner@auth.test", owner_password)
    _membership(db, tenant_a, owner_user.id, MembershipRole.OWNER)

    dual_clinic_password = "another very long passphrase!!"
    dual_clinic_user = _make_user(db, "dual@auth.test", dual_clinic_password)
    _membership(db, tenant_a, dual_clinic_user.id, MembershipRole.MANAGER)
    _membership(db, tenant_b, dual_clinic_user.id, MembershipRole.OWNER)

    no_membership_password = "yet another long passphrase!!!"
    no_membership_user = _make_user(db, "solo@auth.test", no_membership_password)

    inactive_account_password = "inactive account passphrase!!"
    inactive_account_user = _make_user(
        db,
        "inactive-account@auth.test",
        inactive_account_password,
        status=UserAccountStatus.INACTIVE,
    )

    inactive_membership_password = "inactive membership pass!!!!"
    inactive_membership_user = _make_user(
        db, "inactive-member@auth.test", inactive_membership_password
    )
    _membership(
        db,
        tenant_a,
        inactive_membership_user.id,
        MembershipRole.OPERATOR,
        status=MembershipStatus.INACTIVE,
    )

    return AuthTenancy(
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        owner_user=owner_user,
        owner_password=owner_password,
        dual_clinic_user=dual_clinic_user,
        dual_clinic_password=dual_clinic_password,
        no_membership_user=no_membership_user,
        no_membership_password=no_membership_password,
        inactive_account_user=inactive_account_user,
        inactive_account_password=inactive_account_password,
        inactive_membership_user=inactive_membership_user,
        inactive_membership_password=inactive_membership_password,
    )
