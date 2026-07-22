"""Service for staff (tenant membership) administration.

Re-checks the role permitted for each action itself (authoritative - see
`app.core.authorization`) rather than trusting only the API-layer
dependency, and re-derives every business rule from freshly-read rows -
never from client-supplied role/status values for anyone other than the
target of the request. Owns the commit for every mutation and commits
BEFORE emitting a `SUCCESS` audit event - never after.

Authorization matrix (see tasks/current/task.md "Roles" section):
  * OWNER may invite/change/deactivate/remove any role.
  * MANAGER's permitted target set is action-dependent - task.md
    deliberately draws this distinction, it is not a single uniform rule:
    - invite (create), role change, and remove (delete) are restricted to
      memberships whose CURRENT role is OPERATOR or AUDITOR
      (`_can_manager_administer_target`);
    - activate/deactivate (a `status`-only update) is allowed for ANY
      non-OWNER target, including MANAGER and CONTENT_EDITOR
      (`_can_manager_change_status`) - task.md: "activate/deactivate
      non-owner memberships."
    A manager may also never grant OWNER to anyone, and never mutate an
    OWNER membership through any of the above.
  * OPERATOR/CONTENT_EDITOR/AUDITOR may never mutate staff.
  * No one may elevate their own role (see `_ROLE_RANK` below).
  * The clinic's last active OWNER can never be demoted, deactivated, or
    removed - enforced with a row lock (`lock_active_owner_ids`) to close
    the obvious concurrent-request race.
"""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import AuditEvent, AuditOutcome, emit_audit_event
from app.core.authorization import STAFF_MANAGE_ROLES, STAFF_READ_ROLES, require_role
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.tenant_context import TenantContext
from app.models.membership import MembershipRole, MembershipStatus, TenantMembership
from app.repositories.membership import MembershipRepository

_RESOURCE_TYPE = "membership"

# Must match app.models.membership.TenantMembership's UniqueConstraint name
# exactly - this is how a duplicate-membership race (both requests pass the
# pre-check in `create()`, one loses at the database) is distinguished from
# any other IntegrityError, so only this specific violation is ever
# translated into a 409 (see `_is_duplicate_membership_violation`).
_MEMBERSHIP_UNIQUE_CONSTRAINT = "uq_tenant_memberships_tenant_user"


def _is_duplicate_membership_violation(exc: IntegrityError) -> bool:
    """True only for the `(tenant_id, user_id)` unique-constraint violation
    - never for any other integrity error - so a genuinely unexpected
    database integrity failure still surfaces as an unhandled error
    instead of being silently mislabeled as a duplicate-membership
    conflict. Relies on the driver's structured constraint-name diagnostic
    (psycopg's `.diag.constraint_name`), never on parsing the free-text
    error message, which is not a stable contract across Postgres/driver
    versions."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _MEMBERSHIP_UNIQUE_CONSTRAINT


# Coarse privilege ranking used only to reject SELF role changes that would
# increase the caller's own privilege ("self-elevation"). It is deliberately
# not used for anything else (e.g. it does not rank OPERATOR vs AUDITOR
# against each other for actions on OTHER users - the manager/owner rules
# below are the authoritative source for that).
_ROLE_RANK: dict[MembershipRole, int] = {
    MembershipRole.OWNER: 3,
    MembershipRole.MANAGER: 2,
    MembershipRole.OPERATOR: 1,
    MembershipRole.CONTENT_EDITOR: 1,
    MembershipRole.AUDITOR: 1,
}

# The only CURRENT target roles a MANAGER may invite, change the role of,
# or remove - task.md: "Manager may invite operator and auditor roles"
# and "remove operator and auditor memberships." Deliberately NOT used for
# activate/deactivate - see `_can_manager_change_status` below, which is
# broader per the same task.md section.
_MANAGER_ADMINISTRABLE_ROLES: frozenset[MembershipRole] = frozenset(
    {MembershipRole.OPERATOR, MembershipRole.AUDITOR}
)


def _can_manager_administer_target(target_role: MembershipRole) -> bool:
    return target_role in _MANAGER_ADMINISTRABLE_ROLES


def _can_manager_change_status(target_role: MembershipRole) -> bool:
    # task.md: "activate/deactivate non-owner memberships" - any role
    # except OWNER, which is strictly broader than
    # `_can_manager_administer_target` (used for invite/role-change/remove).
    return target_role != MembershipRole.OWNER


class StaffService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = MembershipRepository(db)

    def list(
        self,
        context: TenantContext,
        *,
        role: MembershipRole | None = None,
        status: MembershipStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[TenantMembership], int]:
        require_role(context, STAFF_READ_ROLES)
        return self._repo.list_by_tenant(
            context.tenant_id, role=role, status=status, limit=limit, offset=offset
        )

    def create(
        self, context: TenantContext, user_id: uuid.UUID, role: MembershipRole
    ) -> TenantMembership:
        try:
            require_role(context, STAFF_MANAGE_ROLES)
            self._check_can_assign_role(context, role)
        except ForbiddenError:
            self._audit(context, "membership.create", AuditOutcome.REJECTED)
            raise

        # Fast, friendly rejection for the common case - but the database's
        # unique constraint is the actual arbiter: two concurrent requests
        # for the same (tenant_id, user_id) can both pass this read, so the
        # insert below can still race and must be handled just as safely.
        if self._repo.get_membership(context.tenant_id, user_id) is not None:
            self._audit(context, "membership.create", AuditOutcome.REJECTED)
            raise ConflictError("A membership already exists for this user in this clinic.")

        try:
            membership = self._repo.create(context.tenant_id, user_id, role)
        except IntegrityError as exc:
            self._db.rollback()
            if not _is_duplicate_membership_violation(exc):
                raise
            self._audit(context, "membership.create", AuditOutcome.REJECTED)
            raise ConflictError(
                "A membership already exists for this user in this clinic."
            ) from exc

        self._db.commit()
        self._audit(context, "membership.create", AuditOutcome.SUCCESS, membership.id)
        return membership

    def update(
        self,
        context: TenantContext,
        membership_id: uuid.UUID,
        *,
        role: MembershipRole | None = None,
        status: MembershipStatus | None = None,
    ) -> TenantMembership:
        try:
            require_role(context, STAFF_MANAGE_ROLES)
        except ForbiddenError:
            self._audit(context, "membership.update", AuditOutcome.REJECTED, membership_id)
            raise

        target = self._repo.get_by_id(context.tenant_id, membership_id)
        if target is None:
            self._audit(context, "membership.update", AuditOutcome.REJECTED, membership_id)
            raise NotFoundError()

        try:
            self._validate_update(context, target, role, status)
            if target.role == MembershipRole.OWNER and self._is_demoting_or_deactivating_owner(
                role, status
            ):
                self._assert_not_last_active_owner(context.tenant_id, membership_id)
        except (ForbiddenError, ConflictError):
            self._audit(context, "membership.update", AuditOutcome.REJECTED, membership_id)
            raise

        updated = self._repo.update(context.tenant_id, membership_id, role=role, status=status)
        if updated is None:
            self._audit(context, "membership.update", AuditOutcome.REJECTED, membership_id)
            raise NotFoundError()

        self._db.commit()
        if role is not None:
            self._audit(context, "membership.role_changed", AuditOutcome.SUCCESS, membership_id)
        if status is not None:
            event_type = (
                "membership.activated"
                if status == MembershipStatus.ACTIVE
                else "membership.deactivated"
            )
            self._audit(context, event_type, AuditOutcome.SUCCESS, membership_id)
        return updated

    def delete(self, context: TenantContext, membership_id: uuid.UUID) -> None:
        try:
            require_role(context, STAFF_MANAGE_ROLES)
        except ForbiddenError:
            self._audit(context, "membership.removed", AuditOutcome.REJECTED, membership_id)
            raise

        target = self._repo.get_by_id(context.tenant_id, membership_id)
        if target is None:
            self._audit(context, "membership.removed", AuditOutcome.REJECTED, membership_id)
            raise NotFoundError()

        try:
            if context.role == MembershipRole.MANAGER and not _can_manager_administer_target(
                target.role
            ):
                raise ForbiddenError("Not permitted to perform this action.")
            if target.role == MembershipRole.OWNER:
                self._assert_not_last_active_owner(context.tenant_id, membership_id)
        except (ForbiddenError, ConflictError):
            self._audit(context, "membership.removed", AuditOutcome.REJECTED, membership_id)
            raise

        # Soft-deactivation, not physical deletion: `status` is already the
        # documented membership lifecycle (see app/models/membership.py), a
        # deactivated membership administers nothing (fails every
        # `resolve_membership` re-check), and no audit event or other
        # record ever holds a foreign key to a membership row that physical
        # deletion could break.
        updated = self._repo.update(
            context.tenant_id, membership_id, status=MembershipStatus.INACTIVE
        )
        if updated is None:
            self._audit(context, "membership.removed", AuditOutcome.REJECTED, membership_id)
            raise NotFoundError()

        self._db.commit()
        self._audit(context, "membership.removed", AuditOutcome.SUCCESS, membership_id)

    def _check_can_assign_role(self, context: TenantContext, role: MembershipRole) -> None:
        # Invite is a special case of "administering a target": the
        # not-yet-existing membership's role is treated as the target role.
        if context.role == MembershipRole.MANAGER and not _can_manager_administer_target(role):
            raise ForbiddenError("Not permitted to perform this action.")

    def _validate_update(
        self,
        context: TenantContext,
        target: TenantMembership,
        role: MembershipRole | None,
        status: MembershipStatus | None,
    ) -> None:
        if role is None and status is None:
            return
        if context.role == MembershipRole.MANAGER:
            if role is not None and (
                role == MembershipRole.OWNER or not _can_manager_administer_target(target.role)
            ):
                raise ForbiddenError("Not permitted to perform this action.")
            if status is not None and not _can_manager_change_status(target.role):
                raise ForbiddenError("Not permitted to perform this action.")
        if role is not None:
            if target.user_id == context.user_id and _ROLE_RANK[role] > _ROLE_RANK[target.role]:
                raise ForbiddenError("Not permitted to perform this action.")

    @staticmethod
    def _is_demoting_or_deactivating_owner(
        role: MembershipRole | None, status: MembershipStatus | None
    ) -> bool:
        role_changed_away = role is not None and role != MembershipRole.OWNER
        deactivated = status is not None and status != MembershipStatus.ACTIVE
        return role_changed_away or deactivated

    def _assert_not_last_active_owner(self, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> None:
        active_owner_ids = self._repo.lock_active_owner_ids(tenant_id)
        if membership_id in active_owner_ids and len(active_owner_ids) <= 1:
            raise ConflictError("The clinic must always have at least one active owner.")

    def _audit(
        self,
        context: TenantContext,
        event_type: str,
        outcome: AuditOutcome,
        membership_id: uuid.UUID | None = None,
    ) -> None:
        emit_audit_event(
            AuditEvent(
                event_type=event_type,
                actor_user_id=context.user_id,
                target_resource_type=_RESOURCE_TYPE,
                outcome=outcome,
                tenant_id=context.tenant_id,
                target_resource_id=membership_id,
            )
        )
