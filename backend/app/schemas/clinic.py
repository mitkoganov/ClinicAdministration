import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.membership import MembershipRole
from app.models.tenant import TenantStatus


class ClinicRead(BaseModel):
    """`role` is the caller's own role in this clinic, not a tenant column -
    always built explicitly from the resolved `TenantContext`, never via
    `model_validate` directly on a `Tenant` ORM object."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    status: TenantStatus
    role: MembershipRole


class ClinicUpdate(BaseModel):
    """`extra="forbid"` rejects any field beyond the explicit allowlist below
    (e.g. `status`, `slug`, `id`) with a 422 instead of silently ignoring it -
    task.md requires PATCH /clinic to allow only display name for now."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
