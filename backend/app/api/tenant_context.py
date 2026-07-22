from fastapi import APIRouter, Depends

from app.core.tenant_context import TenantContext, get_tenant_context
from app.schemas.tenant import TenantContextResponse

router = APIRouter(prefix="/api/v1/tenant-context", tags=["tenant-context"])


@router.get("", response_model=TenantContextResponse)
def read_tenant_context(
    context: TenantContext = Depends(get_tenant_context),
) -> TenantContextResponse:
    return TenantContextResponse(
        tenant_id=context.tenant_id,
        tenant_name=context.tenant_name,
        role=context.role,
        membership_status=context.membership_status,
    )
