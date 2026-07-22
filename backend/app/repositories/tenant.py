import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenant import Tenant


class TenantRepository:
    """Read access to tenants. There is deliberately no `list_all` — tenants
    are only ever looked up one at a time, by id or by slug, from a
    server-validated identifier."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, tenant_id: uuid.UUID) -> Tenant | None:
        return self._db.get(Tenant, tenant_id)

    def get_by_slug(self, slug: str) -> Tenant | None:
        stmt = select(Tenant).where(Tenant.slug == slug)
        return self._db.execute(stmt).scalar_one_or_none()

    def update_name(self, tenant_id: uuid.UUID, name: str) -> Tenant | None:
        tenant = self.get_by_id(tenant_id)
        if tenant is None:
            return None
        tenant.name = name
        self._db.flush()
        return tenant
