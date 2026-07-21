import logging
import sys

from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    """Structured, non-sensitive logging. Never log request/response bodies
    or secrets here or in any handler added downstream."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
