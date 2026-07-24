import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.appointment_service_type import AppointmentServiceType, ServiceTypeStatus


class AppointmentServiceTypeRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(
        self, tenant_id: uuid.UUID, service_type_id: uuid.UUID
    ) -> AppointmentServiceType | None:
        stmt = select(AppointmentServiceType).where(
            AppointmentServiceType.tenant_id == tenant_id,
            AppointmentServiceType.id == service_type_id,
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def get_by_code(self, tenant_id: uuid.UUID, code: str) -> AppointmentServiceType | None:
        stmt = select(AppointmentServiceType).where(
            AppointmentServiceType.tenant_id == tenant_id, AppointmentServiceType.code == code
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        status: ServiceTypeStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AppointmentServiceType], int]:
        conditions = [AppointmentServiceType.tenant_id == tenant_id]
        if status is not None:
            conditions.append(AppointmentServiceType.status == status)

        total = self._db.execute(
            select(func.count()).select_from(AppointmentServiceType).where(*conditions)
        ).scalar_one()
        stmt = (
            select(AppointmentServiceType)
            .where(*conditions)
            .order_by(AppointmentServiceType.name.asc(), AppointmentServiceType.id.asc())
            .limit(limit)
            .offset(offset)
        )
        items = list(self._db.execute(stmt).scalars().all())
        return items, total

    def create(
        self,
        tenant_id: uuid.UUID,
        *,
        name: str,
        code: str,
        description: str | None,
        default_duration_minutes: int,
        buffer_before_minutes: int,
        buffer_after_minutes: int,
    ) -> AppointmentServiceType:
        service_type = AppointmentServiceType(
            tenant_id=tenant_id,
            name=name,
            code=code,
            description=description,
            default_duration_minutes=default_duration_minutes,
            buffer_before_minutes=buffer_before_minutes,
            buffer_after_minutes=buffer_after_minutes,
        )
        self._db.add(service_type)
        self._db.flush()
        return service_type

    def update(
        self,
        tenant_id: uuid.UUID,
        service_type_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        default_duration_minutes: int | None = None,
        buffer_before_minutes: int | None = None,
        buffer_after_minutes: int | None = None,
        status: ServiceTypeStatus | None = None,
    ) -> AppointmentServiceType | None:
        service_type = self.get_by_id(tenant_id, service_type_id)
        if service_type is None:
            return None
        if name is not None:
            service_type.name = name
        if description is not None:
            service_type.description = description
        if default_duration_minutes is not None:
            service_type.default_duration_minutes = default_duration_minutes
        if buffer_before_minutes is not None:
            service_type.buffer_before_minutes = buffer_before_minutes
        if buffer_after_minutes is not None:
            service_type.buffer_after_minutes = buffer_after_minutes
        if status is not None:
            service_type.status = status
        self._db.flush()
        return service_type
