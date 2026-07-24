import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.authorization import CALENDAR_READ_ROLES, require_roles
from app.core.csrf import require_csrf
from app.core.tenant_context import TenantContext, get_tenant_context
from app.db.session import get_db
from app.schemas.calendar import CalendarBlockCreate, CalendarBlockRead, CalendarBlockUpdate
from app.services.calendar_block_service import CalendarBlockService

router = APIRouter(prefix="/api/v1/calendar-blocks", tags=["calendar-blocks"])


@router.get("", response_model=list[CalendarBlockRead])
def list_blocks(
    date_from: datetime = Query(...),
    date_to: datetime = Query(...),
    provider_id: uuid.UUID | None = Query(default=None),
    room_id: uuid.UUID | None = Query(default=None),
    context: TenantContext = Depends(require_roles(*CALENDAR_READ_ROLES)),
    db: Session = Depends(get_db),
) -> list[CalendarBlockRead]:
    blocks = CalendarBlockService(db).list_in_range(
        context,
        range_start=date_from,
        range_end=date_to,
        provider_user_id=provider_id,
        room_id=room_id,
    )
    return [CalendarBlockRead.model_validate(b) for b in blocks]


@router.post("", response_model=CalendarBlockRead, dependencies=[Depends(require_csrf)])
def create_block(
    payload: CalendarBlockCreate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> CalendarBlockRead:
    block = CalendarBlockService(db).create(
        context,
        provider_user_id=payload.provider_user_id,
        room_id=payload.room_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        reason=payload.reason,
        block_type=payload.block_type,
    )
    return CalendarBlockRead.model_validate(block)


@router.get("/{block_id}", response_model=CalendarBlockRead)
def get_block(
    block_id: uuid.UUID,
    context: TenantContext = Depends(require_roles(*CALENDAR_READ_ROLES)),
    db: Session = Depends(get_db),
) -> CalendarBlockRead:
    block = CalendarBlockService(db).get(context, block_id)
    return CalendarBlockRead.model_validate(block)


@router.patch("/{block_id}", response_model=CalendarBlockRead, dependencies=[Depends(require_csrf)])
def update_block(
    block_id: uuid.UUID,
    payload: CalendarBlockUpdate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> CalendarBlockRead:
    block = CalendarBlockService(db).update(
        context,
        block_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        reason=payload.reason,
        block_type=payload.block_type,
    )
    return CalendarBlockRead.model_validate(block)


@router.delete(
    "/{block_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_csrf)]
)
def remove_block(
    block_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> None:
    CalendarBlockService(db).remove(context, block_id)
