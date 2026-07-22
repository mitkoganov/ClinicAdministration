import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.authorization import READ_ROLES, require_roles
from app.core.tenant_context import TenantContext, get_tenant_context
from app.db.session import get_db
from app.schemas.tenant_scoped_record import (
    TenantScopedRecordCreate,
    TenantScopedRecordRead,
    TenantScopedRecordUpdate,
)
from app.services.tenant_scoped_record_service import TenantScopedRecordService

router = APIRouter(prefix="/api/v1/tenant-resources", tags=["tenant-resources"])

# Mutation routes (create/update/delete) deliberately do NOT use an
# API-layer `require_roles(...)` early rejection: that would reject before
# the service ever runs, and the service is where insufficient-role
# rejections get audited (see task.md's audit requirements). The service's
# own `require_role` call is the actual, authoritative authorization
# boundary for every mutation — see app.core.authorization / AGENTS.md.
#
# The service also owns the commit for each mutation (immediately before
# its success audit event - see tenant_scoped_record_service.py), so routes
# never call `db.commit()` themselves.


@router.post("", response_model=TenantScopedRecordRead, status_code=status.HTTP_201_CREATED)
def create_tenant_resource(
    payload: TenantScopedRecordCreate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TenantScopedRecordRead:
    record = TenantScopedRecordService(db).create(context, payload.name)
    return TenantScopedRecordRead.model_validate(record)


@router.get("", response_model=list[TenantScopedRecordRead])
def list_tenant_resources(
    context: TenantContext = Depends(require_roles(*READ_ROLES)),
    db: Session = Depends(get_db),
) -> list[TenantScopedRecordRead]:
    records = TenantScopedRecordService(db).list(context)
    return [TenantScopedRecordRead.model_validate(record) for record in records]


@router.get("/{record_id}", response_model=TenantScopedRecordRead)
def get_tenant_resource(
    record_id: uuid.UUID,
    context: TenantContext = Depends(require_roles(*READ_ROLES)),
    db: Session = Depends(get_db),
) -> TenantScopedRecordRead:
    record = TenantScopedRecordService(db).get(context, record_id)
    return TenantScopedRecordRead.model_validate(record)


@router.put("/{record_id}", response_model=TenantScopedRecordRead)
def update_tenant_resource(
    record_id: uuid.UUID,
    payload: TenantScopedRecordUpdate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TenantScopedRecordRead:
    record = TenantScopedRecordService(db).update(context, record_id, payload.name)
    return TenantScopedRecordRead.model_validate(record)


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant_resource(
    record_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> None:
    TenantScopedRecordService(db).delete(context, record_id)
