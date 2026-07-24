import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base

# A generous but bounded ceiling - guards against accidental garbage input
# (e.g. a duration typo of minutes-as-seconds), not a real clinical limit.
MAX_SERVICE_DURATION_MINUTES = 1440
MAX_BUFFER_MINUTES = 480


class ServiceTypeStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class AppointmentServiceType(Base):
    """A billable-in-spirit-but-not-in-this-task type of visit/procedure a
    tenant offers. Deliberately carries no price/currency/billing/insurance
    field - billing is out of MED-005's scope (see tasks/current/task.md
    Non-goals)."""

    __tablename__ = "appointment_service_types"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_appointment_service_types_tenant_code"),
        Index("ix_appointment_service_types_tenant_id", "tenant_id"),
        CheckConstraint(
            "length(btrim(name)) > 0", name="ck_appointment_service_types_name_not_blank"
        ),
        CheckConstraint(
            "length(btrim(code)) > 0", name="ck_appointment_service_types_code_not_blank"
        ),
        CheckConstraint(
            "default_duration_minutes > 0 AND default_duration_minutes <= "
            f"{MAX_SERVICE_DURATION_MINUTES}",
            name="ck_appointment_service_types_duration_range",
        ),
        CheckConstraint(
            f"buffer_before_minutes >= 0 AND buffer_before_minutes <= {MAX_BUFFER_MINUTES}",
            name="ck_appointment_service_types_buffer_before_range",
        ),
        CheckConstraint(
            f"buffer_after_minutes >= 0 AND buffer_after_minutes <= {MAX_BUFFER_MINUTES}",
            name="ck_appointment_service_types_buffer_after_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    default_duration_minutes: Mapped[int] = mapped_column(nullable=False)
    buffer_before_minutes: Mapped[int] = mapped_column(nullable=False, default=0)
    buffer_after_minutes: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[ServiceTypeStatus] = mapped_column(
        SAEnum(
            ServiceTypeStatus,
            name="service_type_status",
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ServiceTypeStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
