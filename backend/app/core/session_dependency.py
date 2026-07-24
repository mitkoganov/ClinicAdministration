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
    never also mean "a session cookie was sent but turned out invalid"
    OR "a session cookie was sent and validating it hit a transient
    infrastructure problem". Both `InvalidSessionError` (unknown/revoked/
    expired/inactive-account - a subclass of `UnauthorizedError`) and the
    plainer `UnauthorizedError` `validate_session` raises for a
    touch-commit failure must propagate unchanged, never collapse to
    `None` here:

    - A stale `InvalidSessionError` collapsing to `None` would make
      `get_tenant_context`'s dev-identity fallback treat an
      attacker-controlled or merely-stale production cookie as if it
      were absent, and would skip the dedicated cookie-clearing handler
      (see app.core.session_cookies).
    - A transient `UnauthorizedError` collapsing to `None` is worse: this
      dependency backs `require_csrf` (see app.core.csrf), and `None`
      there means "CSRF does not apply" - so a mutating request whose
      session touch-refresh commit merely hiccuped would skip CSRF
      enforcement entirely, even though the same request's OTHER auth
      dependency (`get_current_session`) might independently re-validate
      and succeed moments later. A caught-but-genuinely-transient error
      must fail the request, not silently downgrade its auth to `None`.

    This function therefore never catches anything from
    `validate_session` - only a genuinely missing cookie short-circuits
    to `None`."""
    raw_token = _extract_session_token(request)
    if not raw_token:
        return None
    return SessionService(db, settings).validate_session(raw_token)


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
    dedicated handler instead of silently treating them as absent.

    Deliberately catches only `InvalidSessionError`, not the broader
    `UnauthorizedError` it subclasses: `validate_session` also raises the
    plain `UnauthorizedError` for a transient touch-refresh commit
    failure (see its docstring) - the session itself may still be
    perfectly valid, the request just failed to record its own activity.
    Collapsing THAT into `None` would make logout return a false 200
    (idempotent-no-session success) without ever having revoked a session
    that is still live server-side. Letting it propagate instead reaches
    the generic `AppError` handler - a plain 401, no cookie clearing, no
    exception detail - exactly like every other route already gets for
    this same failure, so this fix changes no other route's behavior."""
    raw_token = _extract_session_token(request)
    if not raw_token:
        return None
    try:
        return SessionService(db, settings).validate_session(raw_token)
    except InvalidSessionError:
        return None
