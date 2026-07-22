import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.membership import MembershipRole


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class StatusResponse(BaseModel):
    status: str = "ok"


class ClinicSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: uuid.UUID
    name: str
    role: MembershipRole


class ClinicsResponse(BaseModel):
    items: list[ClinicSummary]


class SelectClinicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID


class MeResponse(BaseModel):
    """Deliberately excludes password hash, session hash, and any
    reset/invitation token hash - see task.md "GET /auth/me" for the
    exact allowlist this schema encodes."""

    user_id: uuid.UUID
    email: str
    display_name: str
    selected_clinic: ClinicSummary | None
    role: MembershipRole | None
    session_expires_at: datetime


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class PasswordResetRequestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)


class PasswordResetConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)
    new_password: str = Field(min_length=1, max_length=256)


class InvitationAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=256)
