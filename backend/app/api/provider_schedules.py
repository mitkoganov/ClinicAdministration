import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.authorization import CALENDAR_READ_ROLES, require_roles
from app.core.csrf import require_csrf
from app.core.tenant_context import TenantContext, get_tenant_context
from app.db.session import get_db
from app.models.provider_schedule import ProviderSchedule, ProviderScheduleStatus
from app.schemas.calendar import (
    ProviderScheduleCreate,
    ProviderScheduleList,
    ProviderScheduleRead,
    ProviderScheduleUpdate,
    ProviderScheduleWithBreaksRead,
    ScheduleBreakRead,
)
from app.services.schedule_service import ScheduleService

router = APIRouter(prefix="/api/v1/provider-schedules", tags=["provider-schedules"])


def _with_breaks(
    service: ScheduleService, context: TenantContext, schedule: ProviderSchedule
) -> ProviderScheduleWithBreaksRead:
    breaks = service.get_breaks(context, schedule.id)
    return ProviderScheduleWithBreaksRead(
        **ProviderScheduleRead.model_validate(schedule).model_dump(),
        breaks=[ScheduleBreakRead.model_validate(b) for b in breaks],
    )


@router.get("", response_model=ProviderScheduleList)
def list_schedules(
    provider_user_id: uuid.UUID | None = Query(default=None),
    status_filter: ProviderScheduleStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(require_roles(*CALENDAR_READ_ROLES)),
    db: Session = Depends(get_db),
) -> ProviderScheduleList:
    items, total = ScheduleService(db).list(
        context, provider_user_id=provider_user_id, status=status_filter, limit=limit, offset=offset
    )
    return ProviderScheduleList(
        items=[ProviderScheduleRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "", response_model=ProviderScheduleWithBreaksRead, dependencies=[Depends(require_csrf)]
)
def create_schedule(
    payload: ProviderScheduleCreate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ProviderScheduleWithBreaksRead:
    service = ScheduleService(db)
    schedule = service.create(
        context,
        provider_user_id=payload.provider_user_id,
        day_of_week=payload.day_of_week,
        start_time=payload.start_time,
        end_time=payload.end_time,
        effective_from=payload.effective_from,
        effective_until=payload.effective_until,
        room_id=payload.room_id,
        breaks=[(b.start_time, b.end_time, b.label) for b in payload.breaks],
    )
    return _with_breaks(service, context, schedule)


@router.get("/{schedule_id}", response_model=ProviderScheduleWithBreaksRead)
def get_schedule(
    schedule_id: uuid.UUID,
    context: TenantContext = Depends(require_roles(*CALENDAR_READ_ROLES)),
    db: Session = Depends(get_db),
) -> ProviderScheduleWithBreaksRead:
    service = ScheduleService(db)
    schedule = service.get(context, schedule_id)
    return _with_breaks(service, context, schedule)


@router.patch(
    "/{schedule_id}",
    response_model=ProviderScheduleWithBreaksRead,
    dependencies=[Depends(require_csrf)],
)
def update_schedule(
    schedule_id: uuid.UUID,
    payload: ProviderScheduleUpdate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ProviderScheduleWithBreaksRead:
    service = ScheduleService(db)
    schedule = service.update(
        context,
        schedule_id,
        start_time=payload.start_time,
        end_time=payload.end_time,
        effective_from=payload.effective_from,
        effective_until=payload.effective_until,
        room_id=payload.room_id,
        breaks=(
            [(b.start_time, b.end_time, b.label) for b in payload.breaks]
            if payload.breaks is not None
            else None
        ),
    )
    return _with_breaks(service, context, schedule)


@router.post(
    "/{schedule_id}/deactivate",
    response_model=ProviderScheduleWithBreaksRead,
    dependencies=[Depends(require_csrf)],
)
def deactivate_schedule(
    schedule_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ProviderScheduleWithBreaksRead:
    service = ScheduleService(db)
    schedule = service.deactivate(context, schedule_id)
    return _with_breaks(service, context, schedule)
