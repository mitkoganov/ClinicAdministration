import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class TenantScopedRecord(Base):
    """Internal architectural-validation model only.

    This entity exists solely to exercise and test the tenant-scoped
    repository/service/authorization pattern end to end. It is not a
    business-domain entity, carries no clinic/practitioner/patient data, and
    must not be extended with such fields. Future business modules
    (clinics, practitioners, appointments, ...) get their own models that
    follow this same tenant-scoping pattern, not this one."""

    __tablename__ = "tenant_scoped_records"
    __table_args__ = (
        Index("ix_tenant_scoped_records_tenant_id", "tenant_id"),
        # Composite index for the tenant-scoped single-row lookup
        # (WHERE tenant_id = :t AND id = :i) used by the repository layer.
        Index("ix_tenant_scoped_records_tenant_id_id", "tenant_id", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
