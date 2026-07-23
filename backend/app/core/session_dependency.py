"""FastAPI dependencies resolving the current session from the session
cookie. Distinct from `app.core.identity`'s development-only mechanism -
see `app.core.tenant_context.get_tenant_context` for how the two are
combined, with a production session always taking priority.
"""

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import InvalidSessionError, UnauthorizedError
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
    instead of raising when there is genuinely no session to consider -
    used where an absent session is a normal, expected case: CSRF
    checking (which only applies to cookie-authenticated requests) and
    the combined session/dev-identity tenant-context resolution.

    "Optional" only ever means "no session cookie was sent" - it must
    never also mean "a session cookie was sent but turned out invalid".
    `InvalidSessionError` (a subclass of `UnauthorizedError`, raised by
    `validate_session` for every unknown/revoked/expired/inactive-account
    case) is deliberately re-raised, not swallowed, so a stale cookie
    still reaches the dedicated cookie-clearing exception handler (see
    app.core.session_cookies) and still blocks the dev-identity fallback
    in `get_tenant_context` - an attacker-controlled or merely-stale
    production cookie must never be treated as if it were absent. Only
    the narrower, non-session-specific `UnauthorizedError` that
    `validate_session` raises for a touch-commit failure (a transient
    infrastructure problem, not a statement about the cookie's validity)
    is still treated as "no session" here."""
    raw_token = _extract_session_token(request)
    if not raw_token:
        return None
    try:
        return SessionService(db, settings).validate_session(raw_token)
    except InvalidSessionError:
        raise
    except UnauthorizedError:
        return None


def get_current_session_or_none_if_stale(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ValidatedSession | None:
    """Logout-only variant of `get_current_session_optional`: a stale
    session cookie (`InvalidSessionError` - unknown, revoked, expired, or
    an inactive-account session) is treated the same as no session at
    all, rather than propagating to the shared stale-cookie 401 handler.

    This exists ONLY for `POST /auth/logout`, which must stay idempotent
    for a caller who has no *usable* session to revoke - clearing cookies
    and returning success regardless of which unusable-session condition
    applied, never leaking that distinction outward. Every other caller
    must keep using `get_current_session_optional` (which re-raises
    `InvalidSessionError`) so tenant-scoped routes, CSRF checks on other
    mutations, and every other 401 path still clear stale cookies via the
    dedicated handler instead of silently treating them as absent."""
    raw_token = _extract_session_token(request)
    if not raw_token:
        return None
    try:
        return SessionService(db, settings).validate_session(raw_token)
    except UnauthorizedError:
        return None
