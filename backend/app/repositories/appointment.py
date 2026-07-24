import uuid
from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.appointment import BLOCKING_STATUSES, Appointment, AppointmentStatus


class AppointmentRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, tenant_id: uuid.UUID, appointment_id: uuid.UUID) -> Appointment | None:
        stmt = select(Appointment).where(
            Appointment.tenant_id == tenant_id, Appointment.id == appointment_id
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
        provider_user_id: uuid.UUID | None = None,
        room_id: uuid.UUID | None = None,
        service_type_id: uuid.UUID | None = None,
        status: AppointmentStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Appointment], int]:
        conditions = [Appointment.tenant_id == tenant_id]
        if range_start is not None:
            conditions.append(Appointment.ends_at > range_start)
        if range_end is not None:
            conditions.append(Appointment.starts_at < range_end)
        if provider_user_id is not None:
            conditions.append(Appointment.provider_user_id == provider_user_id)
        if room_id is not None:
            conditions.append(Appointment.room_id == room_id)
        if service_type_id is not None:
            conditions.append(Appointment.service_type_id == service_type_id)
        if status is not None:
            conditions.append(Appointment.status == status)

        total = self._db.execute(
            select(func.count()).select_from(Appointment).where(*conditions)
        ).scalar_one()
        stmt = (
            select(Appointment)
            .where(*conditions)
            .order_by(Appointment.starts_at.asc(), Appointment.id.asc())
            .limit(limit)
            .offset(offset)
        )
        items = list(self._db.execute(stmt).scalars().all())
        return items, total

    def list_blocking_in_range(
        self,
        tenant_id: uuid.UUID,
        *,
        range_start: datetime,
        range_end: datetime,
        provider_user_id: uuid.UUID | None = None,
        room_id: uuid.UUID | None = None,
        exclude_appointment_id: uuid.UUID | None = None,
    ) -> list[Appointment]:
        """Every appointment in a BLOCKING status overlapping
        `[range_start, range_end)` - the exact input the availability
        engine subtracts. Cancelled/completed/no-show rows never appear
        here (see app.models.appointment.BLOCKING_STATUSES).
        `exclude_appointment_id` lets a reschedule-availability check
        ignore the very appointment being rescheduled, which would
        otherwise always "block" its own new interval."""
        conditions = [
            Appointment.tenant_id == tenant_id,
            Appointment.status.in_(BLOCKING_STATUSES),
            Appointment.starts_at < range_end,
            Appointment.ends_at > range_start,
        ]
        if provider_user_id is not None:
            conditions.append(Appointment.provider_user_id == provider_user_id)
        if room_id is not None:
            conditions.append(Appointment.room_id == room_id)
        if exclude_appointment_id is not None:
            conditions.append(Appointment.id != exclude_appointment_id)
        stmt = select(Appointment).where(*conditions).order_by(Appointment.starts_at.asc())
        return list(self._db.execute(stmt).scalars().all())

    def create(
        self,
        tenant_id: uuid.UUID,
        *,
        provider_user_id: uuid.UUID,
        room_id: uuid.UUID | None,
        service_type_id: uuid.UUID,
        starts_at: datetime,
        ends_at: datetime,
        patient_display_name: str,
        patient_phone: str | None,
        patient_email: str | None,
        notes: str | None,
        created_by_user_id: uuid.UUID,
    ) -> Appointment:
        appointment = Appointment(
            tenant_id=tenant_id,
            provider_user_id=provider_user_id,
            room_id=room_id,
            service_type_id=service_type_id,
            starts_at=starts_at,
            ends_at=ends_at,
            patient_display_name=patient_display_name,
            patient_phone=patient_phone,
            patient_email=patient_email,
            notes=notes,
            created_by_user_id=created_by_user_id,
            version=1,
        )
        self._db.add(appointment)
        self._db.flush()
        return appointment

    def update_with_version(
        self,
        tenant_id: uuid.UUID,
        appointment_id: uuid.UUID,
        expected_version: int,
        *,
        updated_by_user_id: uuid.UUID,
        values: Mapping[str, object],
    ) -> Appointment | None:
        """Atomic `UPDATE ... WHERE id = :id AND tenant_id = :tenant_id
        AND version = :expected_version` - the database, not a prior
        Python-level read, is the single arbiter of whether the expected
        version still matches. Returns `None` if the row doesn't exist
        (already ruled out by the caller's own prior lookup in normal use)
        OR if the version no longer matches (a genuine concurrent
        modification) - the caller cannot distinguish those two cases from
        this return value alone, which is why every service method calls
        this only after its own `get_by_id` already confirmed existence,
        so a `None` here always means "stale version" in practice."""
        stmt = (
            update(Appointment)
            .where(
                Appointment.tenant_id == tenant_id,
                Appointment.id == appointment_id,
                Appointment.version == expected_version,
            )
            .values(
                **values, version=Appointment.version + 1, updated_by_user_id=updated_by_user_id
            )
            .returning(Appointment)
        )
        result = self._db.execute(stmt)
        updated = result.scalar_one_or_none()
        self._db.flush()
        return updated
