import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class MembershipRole(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    OPERATOR = "operator"
    CONTENT_EDITOR = "content_editor"
    AUDITOR = "auditor"


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class TenantMembership(Base):
    """Connects a user identity to a tenant with a role. `user_id` has no
    foreign key target, deliberately: this predates `UserAccount` (MED-004)
    and is left unconstrained rather than adding a cross-migration FK to the
    already-released MED-002 migration (see CLAUDE.md — never edit a
    released migration). `user_id` values are meant to line up with
    `UserAccount.id` once an account exists, but the database does not
    enforce that itself; see ARCHITECTURE.md — "Authentication and user
    identity"."""

    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_memberships_tenant_user"),
        Index("ix_tenant_memberships_tenant_id", "tenant_id"),
        Index("ix_tenant_memberships_user_id", "user_id"),
        # MED-003: staff-roster listing filters by role and/or status within
        # a tenant (see app.repositories.membership.list_by_tenant) - these
        # composite indexes keep that scoped, filtered, paginated query
        # efficient as a clinic's staff roster grows.
        Index("ix_tenant_memberships_tenant_id_role", "tenant_id", "role"),
        Index("ix_tenant_memberships_tenant_id_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(
        SAEnum(
            MembershipRole,
            name="membership_role",
            native_enum=False,
            length=30,
            # Without this, SQLAlchemy persists the enum MEMBER NAME
            # ("OWNER") instead of its .value ("owner") - store the
            # documented lowercase contract (task.md/ARCHITECTURE.md/
            # SECURITY.md), not the Python identifier.
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    status: Mapped[MembershipStatus] = mapped_column(
        SAEnum(
            MembershipStatus,
            name="membership_status",
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=MembershipStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
