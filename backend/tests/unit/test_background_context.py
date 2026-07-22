import uuid

import pytest

from app.core.background_context import BackgroundTenantContext
from app.core.errors import AppError
from app.models.membership import MembershipRole, TenantMembership
from app.services.tenant_service import resolve_background_execution_context


def test_valid_context_can_be_serialized_and_restored():
    tenant_id = uuid.uuid4()
    actor_user_id = uuid.uuid4()
    context = BackgroundTenantContext(
        tenant_id=tenant_id, actor_user_id=actor_user_id, correlation_id="req-123"
    )

    restored = BackgroundTenantContext.from_dict(context.to_dict())

    assert restored == context


def test_missing_tenant_id_is_rejected():
    with pytest.raises(AppError):
        BackgroundTenantContext.from_dict({"actor_user_id": str(uuid.uuid4())})


def test_missing_actor_user_id_is_rejected():
    with pytest.raises(AppError):
        BackgroundTenantContext.from_dict({"tenant_id": str(uuid.uuid4())})


def test_invalid_identifiers_are_rejected():
    with pytest.raises(AppError):
        BackgroundTenantContext.from_dict(
            {"tenant_id": "not-a-uuid", "actor_user_id": str(uuid.uuid4())}
        )


def test_tenant_owned_operation_without_context_fails_closed():
    with pytest.raises(AppError):
        BackgroundTenantContext.from_dict({})


# --- Revalidation at execution time (requires a real database - see
# app.services.tenant_service.resolve_background_execution_context, the
# explicit entry point background/worker code must call before doing
# anything tenant-owned). ---


@pytest.mark.integration
def test_active_tenant_and_membership_are_revalidated(db_session, tenancy):
    background_context = BackgroundTenantContext(
        tenant_id=tenancy.tenant_a.id, actor_user_id=tenancy.owner_a
    )

    context = resolve_background_execution_context(db_session, background_context)

    assert context.tenant_id == tenancy.tenant_a.id
    assert context.user_id == tenancy.owner_a
    assert context.role == MembershipRole.OWNER


@pytest.mark.integration
def test_deactivated_tenant_is_rejected(db_session, tenancy):
    background_context = BackgroundTenantContext(
        tenant_id=tenancy.inactive_tenant.id, actor_user_id=tenancy.owner_a
    )

    with pytest.raises(AppError):
        resolve_background_execution_context(db_session, background_context)


@pytest.mark.integration
def test_removed_membership_is_rejected(db_session, tenancy):
    background_context = BackgroundTenantContext(
        tenant_id=tenancy.tenant_a.id, actor_user_id=tenancy.stranger
    )

    with pytest.raises(AppError):
        resolve_background_execution_context(db_session, background_context)


@pytest.mark.integration
def test_inactive_membership_is_rejected(db_session, tenancy):
    background_context = BackgroundTenantContext(
        tenant_id=tenancy.tenant_a.id, actor_user_id=tenancy.inactive_member_a
    )

    with pytest.raises(AppError):
        resolve_background_execution_context(db_session, background_context)


@pytest.mark.integration
def test_role_changed_after_enqueue_is_reloaded_at_execution_time(db_session, tenancy):
    # The context is conceptually "enqueued" while the caller is still
    # OPERATOR ...
    background_context = BackgroundTenantContext(
        tenant_id=tenancy.tenant_a.id, actor_user_id=tenancy.operator_a
    )

    # ... but by the time the job actually runs, an admin has promoted them.
    membership = (
        db_session.query(TenantMembership)
        .filter_by(tenant_id=tenancy.tenant_a.id, user_id=tenancy.operator_a)
        .one()
    )
    membership.role = MembershipRole.MANAGER
    db_session.flush()

    context = resolve_background_execution_context(db_session, background_context)

    assert context.role == MembershipRole.MANAGER


@pytest.mark.integration
def test_role_in_stale_payload_is_not_trusted(db_session, tenancy):
    # A malicious or stale payload claims "owner", but BackgroundTenantContext
    # has no role field at all - from_dict must ignore the extra key, and
    # resolution must return the real role from the database (auditor).
    payload = {
        "tenant_id": str(tenancy.tenant_a.id),
        "actor_user_id": str(tenancy.auditor_a),
        "role": "owner",
    }
    background_context = BackgroundTenantContext.from_dict(payload)

    context = resolve_background_execution_context(db_session, background_context)

    assert context.role == MembershipRole.AUDITOR


@pytest.mark.integration
def test_missing_context_fails_with_controlled_application_error(db_session):
    background_context = BackgroundTenantContext(tenant_id=uuid.uuid4(), actor_user_id=uuid.uuid4())

    with pytest.raises(AppError):
        resolve_background_execution_context(db_session, background_context)
