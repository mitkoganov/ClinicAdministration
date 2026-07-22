import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TenantScopedRecordCreate(BaseModel):
    """No `tenant_id` field: ownership is always derived from the validated
    tenant context server-side, never from client input."""

    name: str = Field(min_length=1, max_length=200)


class TenantScopedRecordUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class TenantScopedRecordRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime
