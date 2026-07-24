import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.authorization import CALENDAR_READ_ROLES, require_roles
from app.core.csrf import require_csrf
from app.core.tenant_context import TenantContext, get_tenant_context
from app.db.session import get_db
from app.models.clinic_room import ClinicRoomStatus
from app.schemas.calendar import ClinicRoomCreate, ClinicRoomList, ClinicRoomRead, ClinicRoomUpdate
from app.services.room_service import RoomService

router = APIRouter(prefix="/api/v1/rooms", tags=["rooms"])

# Mutation routes rely on RoomService.require_role as the authoritative
# boundary (see app.services.room_service) - only the read route uses
# `require_roles` as a cheap early-rejection convenience, matching the
# established staff/clinic API pattern.


@router.get("", response_model=ClinicRoomList)
def list_rooms(
    status_filter: ClinicRoomStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(require_roles(*CALENDAR_READ_ROLES)),
    db: Session = Depends(get_db),
) -> ClinicRoomList:
    items, total = RoomService(db).list(context, status=status_filter, limit=limit, offset=offset)
    return ClinicRoomList(
        items=[ClinicRoomRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ClinicRoomRead, dependencies=[Depends(require_csrf)])
def create_room(
    payload: ClinicRoomCreate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ClinicRoomRead:
    room = RoomService(db).create(
        context, name=payload.name, code=payload.code, description=payload.description
    )
    return ClinicRoomRead.model_validate(room)


@router.get("/{room_id}", response_model=ClinicRoomRead)
def get_room(
    room_id: uuid.UUID,
    context: TenantContext = Depends(require_roles(*CALENDAR_READ_ROLES)),
    db: Session = Depends(get_db),
) -> ClinicRoomRead:
    room = RoomService(db).get(context, room_id)
    return ClinicRoomRead.model_validate(room)


@router.patch("/{room_id}", response_model=ClinicRoomRead, dependencies=[Depends(require_csrf)])
def update_room(
    room_id: uuid.UUID,
    payload: ClinicRoomUpdate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ClinicRoomRead:
    room = RoomService(db).update(
        context, room_id, name=payload.name, description=payload.description
    )
    return ClinicRoomRead.model_validate(room)


@router.post(
    "/{room_id}/deactivate", response_model=ClinicRoomRead, dependencies=[Depends(require_csrf)]
)
def deactivate_room(
    room_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ClinicRoomRead:
    room = RoomService(db).deactivate(context, room_id)
    return ClinicRoomRead.model_validate(room)
