"""Serializable tenant execution context for future background jobs.

No job queue exists yet — this only defines the contract a future worker
must accept: the minimum identifiers required to revalidate authorization
before doing anything tenant-owned. It never carries a database object, a
membership object, a role, or an active-state flag — only identifiers. A
worker receiving this context MUST call
`app.services.tenant_service.resolve_background_execution_context` before
acting; this context is not authorization by itself, exactly like the
request-level `TenantContext`, and the role/active-state it produces always
comes from a fresh database read, never from anything captured when the
job was enqueued.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from app.core.errors import AppError


@dataclass(frozen=True)
class BackgroundTenantContext:
    tenant_id: uuid.UUID
    actor_user_id: uuid.UUID
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": str(self.tenant_id),
            "actor_user_id": str(self.actor_user_id),
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackgroundTenantContext":
        tenant_id_raw = data.get("tenant_id")
        actor_user_id_raw = data.get("actor_user_id")

        if not tenant_id_raw:
            raise AppError("Background tenant context missing tenant_id", status_code=400)
        if not actor_user_id_raw:
            raise AppError("Background tenant context missing actor_user_id", status_code=400)

        try:
            tenant_id = uuid.UUID(str(tenant_id_raw))
            actor_user_id = uuid.UUID(str(actor_user_id_raw))
        except ValueError as exc:
            raise AppError(
                "Background tenant context has invalid identifiers", status_code=400
            ) from exc

        return cls(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            correlation_id=data.get("correlation_id"),
        )
