import re
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
    column,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.models.user_account import normalize_email

MAX_NOTES_LENGTH = 2000
MAX_CANCELLATION_REASON_LENGTH = 300

# Blocking statuses participate in the DB-level overlap exclusion
# constraints below; CANCELLED/COMPLETED/NO_SHOW never do, matching
# tasks/current/task.md's availability policy ("cancelled appointments
# never block; completed/no-show are historical"). Kept as a plain tuple
# (not the AppointmentStatus enum directly) because it is embedded into
# raw SQL text for the constraints' partial WHERE clause below.
BLOCKING_STATUSES = ("scheduled", "confirmed")

_BLOCKING_STATUS_SQL = "('" + "', '".join(BLOCKING_STATUSES) + "')"

# A deliberately minimal, documented phone normalization - not a full
# E.164 parser/validator (no external dependency added for this
# foundation slice). Strips everything except digits and a single
# leading "+", so cosmetic formatting differences (spaces, dashes,
# parentheses) don't create spurious distinct values, without claiming
# real phone-number validation.
_PHONE_STRIP_PATTERN = re.compile(r"[^\d+]")


def normalize_patient_phone(value: str) -> str:
    stripped = _PHONE_STRIP_PATTERN.sub("", value.strip())
    if stripped.count("+") > 1 or ("+" in stripped and not stripped.startswith("+")):
        raise ValueError("Phone number may contain only a single leading '+'.")
    digits = stripped.lstrip("+")
    if not digits:
        raise ValueError("Phone number must contain at least one digit.")
    return stripped


class AppointmentStatus(StrEnum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class Appointment(Base):
    """A booked time interval - half-open `[starts_at, ends_at)` - for one
    provider (bare `provider_user_id`, no FK, mirroring
    `TenantMembership.user_id`'s precedent), optionally in one room,
    against one `AppointmentServiceType`.

    Carries only a minimal PATIENT CONTACT SNAPSHOT (display name +
    optional phone/email) - deliberately no `patients` table or FK; a
    future patient-registry task may normalize this later (see
    tasks/current/task.md Non-goals). `notes` is operational scheduling
    context only, never clinical/medical information.

    `version` is this codebase's first optimistic-locking column: every
    mutation increments it and every update statement includes
    `WHERE version = :expected_version` (see
    app.repositories.appointment.AppointmentRepository) - a 0-row update
    result means a concurrent modification happened, mapped to a
    `409 stale_version` domain error. Chosen over `SELECT ... FOR UPDATE`
    for this row specifically because it doesn't hold a lock across an
    entire request (availability checks between the read and the write
    can be slow) and is simpler to test deterministically.

    Double-booking protection is enforced by TWO PostgreSQL exclusion
    constraints below (provider overlap, room overlap) - both scoped to
    `tenant_id` equality and restricted to `BLOCKING_STATUSES` via a
    partial `WHERE` clause, using `tstzrange(starts_at, ends_at, '[)')`
    range overlap (`&&`). This is the first use of this pattern in the
    codebase - see tasks/current/task.md "Current architecture
    assumptions" - and requires the `btree_gist` extension (enabled in
    this task's migration)."""

    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_tenant_id", "tenant_id"),
        Index(
            "ix_appointments_tenant_provider_range", "tenant_id", "provider_user_id", "starts_at"
        ),
        Index("ix_appointments_tenant_room_range", "tenant_id", "room_id", "starts_at"),
        Index("ix_appointments_tenant_status", "tenant_id", "status"),
        CheckConstraint("starts_at < ends_at", name="ck_appointments_start_before_end"),
        CheckConstraint(
            "length(btrim(patient_display_name)) > 0",
            name="ck_appointments_patient_display_name_not_blank",
        ),
        CheckConstraint(
            f"length(cancellation_reason) <= {MAX_CANCELLATION_REASON_LENGTH}",
            name="ck_appointments_cancellation_reason_length",
        ),
        CheckConstraint(
            f"length(notes) <= {MAX_NOTES_LENGTH}", name="ck_appointments_notes_length"
        ),
        CheckConstraint("version >= 1", name="ck_appointments_version_positive"),
        # Provider overlap: no two BLOCKING appointments for the same
        # (tenant, provider) may have overlapping [starts_at, ends_at).
        ExcludeConstraint(
            ("tenant_id", "="),
            ("provider_user_id", "="),
            (
                func.tstzrange(column("starts_at"), column("ends_at"), text("'[)'")),
                "&&",
            ),
            name="ex_appointments_provider_overlap",
            using="gist",
            where=text(f"status IN {_BLOCKING_STATUS_SQL}"),
        ),
        # Room overlap: same, scoped to (tenant, room) - a NULL room_id
        # never participates (Postgres `=` on NULL is never true, so a
        # row with room_id IS NULL cannot equal any other row's room_id
        # under this operator; the additional `room_id IS NOT NULL` in the
        # WHERE clause makes that explicit rather than relying on that
        # NULL-handling nuance alone).
        ExcludeConstraint(
            ("tenant_id", "="),
            ("room_id", "="),
            (
                func.tstzrange(column("starts_at"), column("ends_at"), text("'[)'")),
                "&&",
            ),
            name="ex_appointments_room_overlap",
            using="gist",
            where=text(f"room_id IS NOT NULL AND status IN {_BLOCKING_STATUS_SQL}"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    provider_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    room_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clinic_rooms.id"), nullable=True
    )
    service_type_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("appointment_service_types.id"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AppointmentStatus] = mapped_column(
        SAEnum(
            AppointmentStatus,
            name="appointment_status",
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=AppointmentStatus.SCHEDULED,
    )
    patient_display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    patient_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    patient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @validates("patient_email")
    def _validate_patient_email(self, key: str, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        return normalize_email(value)

    @validates("patient_phone")
    def _validate_patient_phone(self, key: str, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        return normalize_patient_phone(value)
