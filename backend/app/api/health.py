import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.qdrant_client import get_qdrant_client
from app.db.redis_client import get_redis_client
from app.db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness check: process is up. Does not touch dependencies."""
    return {"status": "ok"}


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict[str, object]:
    """Readiness check: verifies dependencies are reachable. Each dependency
    failure is caught independently so one outage doesn't mask the others."""
    checks: dict[str, str] = {}

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("database readiness check failed: %s", exc.__class__.__name__)
        checks["database"] = "unavailable"

    try:
        get_redis_client().ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis readiness check failed: %s", exc.__class__.__name__)
        checks["redis"] = "unavailable"

    try:
        get_qdrant_client().get_collections()
        checks["qdrant"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("qdrant readiness check failed: %s", exc.__class__.__name__)
        checks["qdrant"] = "unavailable"

    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "ok" if all_ok else "degraded", "checks": checks}
