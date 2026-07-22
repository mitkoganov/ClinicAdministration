import uuid

from pydantic import BaseModel

from app.models.membership import MembershipRole, MembershipStatus


class TenantContextResponse(BaseModel):
    """Read-only, non-sensitive projection of the caller's resolved tenant
    context. Never include membership internals (e.g. membership id) or
    database metadata."""

    tenant_id: uuid.UUID
    tenant_name: str
    role: MembershipRole
    membership_status: MembershipStatus
