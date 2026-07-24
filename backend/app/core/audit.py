"""Minimal audit abstraction for tenant-sensitive changes.

No persistent audit store exists yet at the foundation stage. This module is
the documented, safe interim adapter task.md permits: a structured logging
sink behind a stable `emit_audit_event()` call site. Replace the sink inside
`emit_audit_event` with a durable audit repository later without changing any
caller.

Never place secrets, request bodies, or healthcare data in an AuditEvent.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

logger = logging.getLogger("audit")


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    REJECTED = "rejected"


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    # None for events with no known actor - e.g. a login failure against a
    # nonexistent account, where there is no user id to attach (see
    # app.services.auth_service). Every pre-MED-004 caller still passes a
    # real UUID; this widening is backward compatible.
    actor_user_id: uuid.UUID | None
    target_resource_type: str
    outcome: AuditOutcome
    tenant_id: uuid.UUID | None = None
    target_resource_id: uuid.UUID | None = None
    correlation_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "actor_user_id": str(self.actor_user_id) if self.actor_user_id else None,
            "target_resource_type": self.target_resource_type,
            "target_resource_id": (
                str(self.target_resource_id) if self.target_resource_id else None
            ),
            "outcome": self.outcome.value,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp.isoformat(),
        }


def emit_audit_event(event: AuditEvent) -> None:
    logger.info("audit_event", extra={"audit_event": event.to_dict()})
