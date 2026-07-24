import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.tenant_context import TenantContext, get_tenant_context
from app.db.session import get_db
from app.schemas.calendar import AvailabilityRead, AvailableSlotRead
from app.services.availability_service import AvailabilityService

router = APIRouter(prefix="/api/v1/availability", tags=["availability"])


@router.get("", response_model=AvailabilityRead)
def get_availability(
    provider_id: uuid.UUID = Query(...),
    service_type_id: uuid.UUID = Query(...),
    date_from: date = Query(...),
    date_to: date = Query(...),
    room_id: uuid.UUID | None = Query(default=None),
    # Not `require_roles(*CALENDAR_READ_ROLES)` - task.md's authorization
    # matrix grants every active member self-scoped availability access
    # regardless of role; `AvailabilityService.get_availability` is the
    # authoritative check (`require_calendar_read_or_self`), so the API
    # layer only resolves tenant context here, it does not pre-reject.
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> AvailabilityRead:
    result = AvailabilityService(db).get_availability(
        context,
        provider_user_id=provider_id,
        service_type_id=service_type_id,
        date_from=date_from,
        date_to=date_to,
        room_id=room_id,
    )
    return AvailabilityRead(
        tenant_timezone=result.tenant_timezone,
        provider_user_id=result.provider_user_id,
        service_type_id=result.service_type_id,
        room_id=result.room_id,
        slots=[AvailableSlotRead(starts_at=s.starts_at, ends_at=s.ends_at) for s in result.slots],
    )
