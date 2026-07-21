from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="Clinic Admin Platform API",
        version="0.1.0",
        description="Foundation service — no business functionality yet.",
    )

    register_error_handlers(app)
    app.include_router(health_router)

    return app


app = create_app()
