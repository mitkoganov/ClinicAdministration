"""FastAPI dependencies resolving the current session from the session
cookie. Distinct from `app.core.identity`'s development-only mechanism -
see `app.core.tenant_context.get_tenant_context` for how the two are
combined, with a production session always taking priority.
"""

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import UnauthorizedError
from app.db.session import get_db
from app.services.session_service import SessionService, ValidatedSession

SESSION_COOKIE_NAME = "session_token"


def _extract_session_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE_NAME)


def get_current_session(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ValidatedSession:
    """The authoritative "is there a valid, current session" dependency -
    used by every route that requires the caller to be logged in
    (`/auth/me`, `/auth/clinics`, `/auth/select-clinic`,
    `/auth/change-password`). Raises `UnauthorizedError` (401) for a
    missing cookie or any invalid/expired/revoked session."""
    raw_token = _extract_session_token(request)
    if not raw_token:
        raise UnauthorizedError()
    return SessionService(db, settings).validate_session(raw_token)


def get_current_session_optional(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ValidatedSession | None:
    """Same validation as `get_current_session`, but returns `None`
    instead of raising when there is no valid session - used where an
    absent session is a normal, expected case: CSRF checking (which only
    applies to cookie-authenticated requests) and the combined
    session/dev-identity tenant-context resolution."""
    raw_token = _extract_session_token(request)
    if not raw_token:
        return None
    try:
        return SessionService(db, settings).validate_session(raw_token)
    except UnauthorizedError:
        return None
