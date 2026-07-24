import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.authorization import CALENDAR_READ_ROLES, require_roles
from app.core.csrf import require_csrf
from app.core.tenant_context import TenantContext, get_tenant_context
from app.db.session import get_db
from app.models.appointment_service_type import ServiceTypeStatus
from app.schemas.calendar import (
    AppointmentServiceTypeCreate,
    AppointmentServiceTypeList,
    AppointmentServiceTypeRead,
    AppointmentServiceTypeUpdate,
)
from app.services.service_type_service import ServiceTypeService

router = APIRouter(prefix="/api/v1/appointment-service-types", tags=["appointment-service-types"])


@router.get("", response_model=AppointmentServiceTypeList)
def list_service_types(
    status_filter: ServiceTypeStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(require_roles(*CALENDAR_READ_ROLES)),
    db: Session = Depends(get_db),
) -> AppointmentServiceTypeList:
    items, total = ServiceTypeService(db).list(
        context, status=status_filter, limit=limit, offset=offset
    )
    return AppointmentServiceTypeList(
        items=[AppointmentServiceTypeRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=AppointmentServiceTypeRead, dependencies=[Depends(require_csrf)])
def create_service_type(
    payload: AppointmentServiceTypeCreate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> AppointmentServiceTypeRead:
    service_type = ServiceTypeService(db).create(
        context,
        name=payload.name,
        code=payload.code,
        description=payload.description,
        default_duration_minutes=payload.default_duration_minutes,
        buffer_before_minutes=payload.buffer_before_minutes,
        buffer_after_minutes=payload.buffer_after_minutes,
    )
    return AppointmentServiceTypeRead.model_validate(service_type)


@router.get("/{service_type_id}", response_model=AppointmentServiceTypeRead)
def get_service_type(
    service_type_id: uuid.UUID,
    context: TenantContext = Depends(require_roles(*CALENDAR_READ_ROLES)),
    db: Session = Depends(get_db),
) -> AppointmentServiceTypeRead:
    service_type = ServiceTypeService(db).get(context, service_type_id)
    return AppointmentServiceTypeRead.model_validate(service_type)


@router.patch(
    "/{service_type_id}",
    response_model=AppointmentServiceTypeRead,
    dependencies=[Depends(require_csrf)],
)
def update_service_type(
    service_type_id: uuid.UUID,
    payload: AppointmentServiceTypeUpdate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> AppointmentServiceTypeRead:
    service_type = ServiceTypeService(db).update(
        context,
        service_type_id,
        name=payload.name,
        description=payload.description,
        default_duration_minutes=payload.default_duration_minutes,
        buffer_before_minutes=payload.buffer_before_minutes,
        buffer_after_minutes=payload.buffer_after_minutes,
    )
    return AppointmentServiceTypeRead.model_validate(service_type)


@router.post(
    "/{service_type_id}/deactivate",
    response_model=AppointmentServiceTypeRead,
    dependencies=[Depends(require_csrf)],
)
def deactivate_service_type(
    service_type_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> AppointmentServiceTypeRead:
    service_type = ServiceTypeService(db).deactivate(context, service_type_id)
    return AppointmentServiceTypeRead.model_validate(service_type)
