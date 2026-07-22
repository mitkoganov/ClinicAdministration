from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.authorization import READ_ROLES, require_roles
from app.core.tenant_context import TenantContext, get_tenant_context
from app.db.session import get_db
from app.schemas.clinic import ClinicRead, ClinicUpdate
from app.services.clinic_service import ClinicService

router = APIRouter(prefix="/api/v1/clinic", tags=["clinic"])

# PATCH deliberately does NOT use an API-layer `require_roles(...)` early
# rejection - the service's own `require_role` call is the authoritative
# boundary, and it is where an insufficient-role rejection gets audited
# (see app.core.authorization / ClinicService). The service also owns the
# commit for the mutation, immediately before its success audit event, so
# this route never calls `db.commit()` itself. GET uses `require_roles` as
# an early-rejection convenience since a rejected read is not audited here.


@router.get("", response_model=ClinicRead)
def get_clinic(
    context: TenantContext = Depends(require_roles(*READ_ROLES)),
    db: Session = Depends(get_db),
) -> ClinicRead:
    tenant = ClinicService(db).get(context)
    return ClinicRead(
        id=tenant.id, name=tenant.name, slug=tenant.slug, status=tenant.status, role=context.role
    )


@router.patch("", response_model=ClinicRead)
def update_clinic(
    payload: ClinicUpdate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ClinicRead:
    tenant = ClinicService(db).update(context, payload.name)
    return ClinicRead(
        id=tenant.id, name=tenant.name, slug=tenant.slug, status=tenant.status, role=context.role
    )
