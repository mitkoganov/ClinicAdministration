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


class ClinicRoomStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ClinicRoom(Base):
    """A physical room/cabinet within a tenant ("clinic" is the
    documented user-facing term for `Tenant` - see ARCHITECTURE.md; there
    is no separate `clinics` table). Deactivating a room never deletes it
    or reassigns/deletes appointments that already reference it - only
    NEW appointments/schedules/blocks may not select an inactive room
    (enforced at the service layer, not by the database, since historical
    references must remain valid)."""

    __tablename__ = "clinic_rooms"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_clinic_rooms_tenant_code"),
        Index("ix_clinic_rooms_tenant_id", "tenant_id"),
        CheckConstraint("length(btrim(name)) > 0", name="ck_clinic_rooms_name_not_blank"),
        CheckConstraint("length(btrim(code)) > 0", name="ck_clinic_rooms_code_not_blank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    status: Mapped[ClinicRoomStatus] = mapped_column(
        SAEnum(
            ClinicRoomStatus,
            name="clinic_room_status",
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ClinicRoomStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
