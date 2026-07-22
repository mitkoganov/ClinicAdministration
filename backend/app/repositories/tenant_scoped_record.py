import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenant_scoped_record import TenantScopedRecord


class TenantScopedRecordRepository:
    """Every method takes `tenant_id` explicitly and issues a single query
    with `tenant_id` in the WHERE clause. There is no method that looks a
    record up by `id` alone: a foreign-tenant row and a missing row must
    produce the identical "no row" result from one query, never a lookup
    followed by a tenant comparison — the latter would briefly determine
    foreign-tenant existence even if never returned to the caller."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, tenant_id: uuid.UUID, name: str) -> TenantScopedRecord:
        record = TenantScopedRecord(tenant_id=tenant_id, name=name)
        self._db.add(record)
        self._db.flush()
        return record

    def get(self, tenant_id: uuid.UUID, record_id: uuid.UUID) -> TenantScopedRecord | None:
        stmt = select(TenantScopedRecord).where(
            TenantScopedRecord.tenant_id == tenant_id,
            TenantScopedRecord.id == record_id,
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def list(self, tenant_id: uuid.UUID) -> list[TenantScopedRecord]:
        stmt = select(TenantScopedRecord).where(TenantScopedRecord.tenant_id == tenant_id)
        return list(self._db.execute(stmt).scalars().all())

    def update(
        self, tenant_id: uuid.UUID, record_id: uuid.UUID, name: str
    ) -> TenantScopedRecord | None:
        record = self.get(tenant_id, record_id)
        if record is None:
            return None
        record.name = name
        self._db.flush()
        return record

    def delete(self, tenant_id: uuid.UUID, record_id: uuid.UUID) -> bool:
        record = self.get(tenant_id, record_id)
        if record is None:
            return False
        self._db.delete(record)
        self._db.flush()
        return True
