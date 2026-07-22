import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.authorization import STAFF_READ_ROLES, require_roles
from app.core.csrf import require_csrf
from app.core.tenant_context import TenantContext, get_tenant_context
from app.db.session import get_db
from app.models.membership import MembershipRole, MembershipStatus
from app.schemas.staff import (
    StaffMemberCreate,
    StaffMemberList,
    StaffMemberRead,
    StaffMemberUpdate,
)
from app.services.staff_service import StaffService

router = APIRouter(prefix="/api/v1/clinic/staff", tags=["staff"])

# Mutation routes (create/update/delete) deliberately do NOT use an
# API-layer `require_roles(...)` early rejection: the service's own
# `require_role` call is the authoritative boundary, and it is where every
# insufficient-role/final-owner/self-elevation rejection gets audited. The
# service also owns the commit for each mutation, so routes never call
# `db.commit()` themselves. The list route uses `require_roles` as an
# early-rejection convenience since a rejected read is not audited here.


@router.get("", response_model=StaffMemberList)
def list_staff(
    role: MembershipRole | None = None,
    status_filter: MembershipStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(require_roles(*STAFF_READ_ROLES)),
    db: Session = Depends(get_db),
) -> StaffMemberList:
    items, total = StaffService(db).list(
        context, role=role, status=status_filter, limit=limit, offset=offset
    )
    return StaffMemberList(
        items=[StaffMemberRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=StaffMemberRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def add_staff_member(
    payload: StaffMemberCreate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> StaffMemberRead:
    membership = StaffService(db).create(context, payload.user_id, payload.role)
    return StaffMemberRead.model_validate(membership)


@router.patch(
    "/{membership_id}", response_model=StaffMemberRead, dependencies=[Depends(require_csrf)]
)
def update_staff_member(
    membership_id: uuid.UUID,
    payload: StaffMemberUpdate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> StaffMemberRead:
    membership = StaffService(db).update(
        context, membership_id, role=payload.role, status=payload.status
    )
    return StaffMemberRead.model_validate(membership)


@router.delete(
    "/{membership_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_csrf)]
)
def remove_staff_member(
    membership_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> None:
    StaffService(db).delete(context, membership_id)
