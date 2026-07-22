import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.membership import MembershipRole, MembershipStatus


class StaffMemberRead(BaseModel):
    """Deliberately excludes anything beyond the identifiers/role/status/
    timestamps a staff-administration UI needs - no secrets, no
    authentication-provider internals exist yet to leak."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    role: MembershipRole
    status: MembershipStatus
    created_at: datetime


class StaffMemberList(BaseModel):
    items: list[StaffMemberRead]
    total: int
    limit: int
    offset: int


class StaffMemberCreate(BaseModel):
    """No `tenant_id` field: membership is always created in the caller's
    own validated clinic, never a client-supplied tenant. For this
    foundation slice, membership creation is provisioning for an existing
    development/test identity (`user_id`) - not an email-invitation flow;
    see ARCHITECTURE.md for the documented limitation."""

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    role: MembershipRole


class StaffMemberUpdate(BaseModel):
    """Partial update: at least one of `role`/`status` must be supplied, or
    the request is meaningless (and would otherwise silently no-op)."""

    model_config = ConfigDict(extra="forbid")

    role: MembershipRole | None = None
    status: MembershipStatus | None = None

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "StaffMemberUpdate":
        if self.role is None and self.status is None:
            raise ValueError("At least one of 'role' or 'status' must be provided.")
        return self
