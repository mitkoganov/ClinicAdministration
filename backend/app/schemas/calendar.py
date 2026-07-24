import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.appointment import AppointmentStatus
from app.models.appointment_service_type import (
    MAX_BUFFER_MINUTES,
    MAX_SERVICE_DURATION_MINUTES,
    ServiceTypeStatus,
)
from app.models.calendar_block import CalendarBlockType
from app.models.clinic_room import ClinicRoomStatus
from app.models.provider_schedule import ProviderScheduleStatus

# --- Rooms ------------------------------------------------------------


class ClinicRoomRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
    description: str | None
    status: ClinicRoomStatus
    created_at: datetime
    updated_at: datetime


class ClinicRoomList(BaseModel):
    items: list[ClinicRoomRead]
    total: int
    limit: int
    offset: int


class ClinicRoomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=50)
    description: str | None = None


class ClinicRoomUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "ClinicRoomUpdate":
        if self.name is None and self.description is None:
            raise ValueError("At least one field must be provided.")
        return self


# --- Service types ------------------------------------------------------


class AppointmentServiceTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
    description: str | None
    default_duration_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int
    status: ServiceTypeStatus
    created_at: datetime
    updated_at: datetime


class AppointmentServiceTypeList(BaseModel):
    items: list[AppointmentServiceTypeRead]
    total: int
    limit: int
    offset: int


class AppointmentServiceTypeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=50)
    description: str | None = None
    default_duration_minutes: int = Field(gt=0, le=MAX_SERVICE_DURATION_MINUTES)
    buffer_before_minutes: int = Field(default=0, ge=0, le=MAX_BUFFER_MINUTES)
    buffer_after_minutes: int = Field(default=0, ge=0, le=MAX_BUFFER_MINUTES)


class AppointmentServiceTypeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    default_duration_minutes: int | None = Field(
        default=None, gt=0, le=MAX_SERVICE_DURATION_MINUTES
    )
    buffer_before_minutes: int | None = Field(default=None, ge=0, le=MAX_BUFFER_MINUTES)
    buffer_after_minutes: int | None = Field(default=None, ge=0, le=MAX_BUFFER_MINUTES)


# --- Provider schedules --------------------------------------------------


class ScheduleBreakInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: time
    end_time: time
    label: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _start_before_end(self) -> "ScheduleBreakInput":
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time.")
        return self


class ScheduleBreakRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    start_time: time
    end_time: time
    label: str | None


class ProviderScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_user_id: uuid.UUID
    day_of_week: int
    start_time: time
    end_time: time
    effective_from: date
    effective_until: date | None
    room_id: uuid.UUID | None
    status: ProviderScheduleStatus
    created_at: datetime
    updated_at: datetime


class ProviderScheduleWithBreaksRead(ProviderScheduleRead):
    breaks: list[ScheduleBreakRead]


class ProviderScheduleList(BaseModel):
    items: list[ProviderScheduleRead]
    total: int
    limit: int
    offset: int


class ProviderScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_user_id: uuid.UUID
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    effective_from: date
    effective_until: date | None = None
    room_id: uuid.UUID | None = None
    breaks: list[ScheduleBreakInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "ProviderScheduleCreate":
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time.")
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("effective_until must not be before effective_from.")
        return self


class ProviderScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: time | None = None
    end_time: time | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    room_id: uuid.UUID | None = None
    breaks: list[ScheduleBreakInput] | None = None


# --- Calendar blocks ------------------------------------------------------


class CalendarBlockRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_user_id: uuid.UUID | None
    room_id: uuid.UUID | None
    starts_at: datetime
    ends_at: datetime
    reason: str
    block_type: CalendarBlockType
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class CalendarBlockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_user_id: uuid.UUID | None = None
    room_id: uuid.UUID | None = None
    starts_at: datetime
    ends_at: datetime
    reason: str = Field(min_length=1, max_length=300)
    block_type: CalendarBlockType

    @model_validator(mode="after")
    def _validate(self) -> "CalendarBlockCreate":
        if self.provider_user_id is None and self.room_id is None:
            raise ValueError("At least one of provider_user_id or room_id must be provided.")
        if self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at.")
        return self


class CalendarBlockUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: datetime | None = None
    ends_at: datetime | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=300)
    block_type: CalendarBlockType | None = None


# --- Availability -----------------------------------------------------


class AvailableSlotRead(BaseModel):
    starts_at: datetime
    ends_at: datetime


class AvailabilityRead(BaseModel):
    tenant_timezone: str
    provider_user_id: uuid.UUID
    service_type_id: uuid.UUID
    room_id: uuid.UUID | None
    slots: list[AvailableSlotRead]


# --- Appointments -----------------------------------------------------


class AppointmentRead(BaseModel):
    """Full appointment view - includes the patient contact snapshot.
    Only served to roles in CALENDAR_CONTACT_VISIBLE_ROLES (see
    app.api.appointments) - AUDITOR gets `AppointmentSummaryRead`
    instead, which omits phone/email entirely."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_user_id: uuid.UUID
    room_id: uuid.UUID | None
    service_type_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    status: AppointmentStatus
    patient_display_name: str
    patient_phone: str | None
    patient_email: str | None
    notes: str | None
    cancellation_reason: str | None
    created_by_user_id: uuid.UUID
    updated_by_user_id: uuid.UUID | None
    version: int
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None


class AppointmentSummaryRead(BaseModel):
    """Redacted view for roles that may see the calendar but not the
    patient contact snapshot (see task.md "Patient contact visibility")."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_user_id: uuid.UUID
    room_id: uuid.UUID | None
    service_type_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    status: AppointmentStatus
    version: int
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None


class AppointmentList(BaseModel):
    items: list[AppointmentRead | AppointmentSummaryRead]
    total: int
    limit: int
    offset: int


class AppointmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_user_id: uuid.UUID
    room_id: uuid.UUID | None = None
    service_type_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    patient_display_name: str = Field(min_length=1, max_length=200)
    patient_phone: str | None = None
    patient_email: str | None = None
    notes: str | None = Field(default=None, max_length=2000)
    override_availability: bool = False
    override_reason: str | None = Field(default=None, max_length=300)


class AppointmentMetadataUpdate(BaseModel):
    """Deliberately does NOT accept `status` - status transitions only
    ever happen through the explicit action endpoints (confirm/cancel/
    complete/no-show/reschedule), never through this generic patch, so a
    client can never smuggle a status change through here."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int
    patient_display_name: str | None = Field(default=None, min_length=1, max_length=200)
    patient_phone: str | None = None
    patient_email: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


class AppointmentRescheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int
    starts_at: datetime
    ends_at: datetime
    provider_user_id: uuid.UUID | None = None
    room_id: uuid.UUID | None = None
    override_availability: bool = False
    override_reason: str | None = Field(default=None, max_length=300)


class AppointmentCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int
    reason: str = Field(min_length=1, max_length=300)


class AppointmentVersionedActionRequest(BaseModel):
    """confirm/complete/no-show all take only the expected version."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int
