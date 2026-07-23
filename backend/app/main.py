from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.clinic import router as clinic_router
from app.api.health import router as health_router
from app.api.staff import router as staff_router
from app.api.tenant_context import router as tenant_context_router
from app.api.tenant_scoped_records import router as tenant_scoped_records_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.session_cookies import register_invalid_session_cookie_clearing


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="Clinic Admin Platform API",
        version="0.1.0",
        description="Foundation service — no business functionality yet.",
    )

    register_error_handlers(app)
    register_invalid_session_cookie_clearing(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(tenant_context_router)
    app.include_router(tenant_scoped_records_router)
    app.include_router(clinic_router)
    app.include_router(staff_router)

    return app


app = create_app()
