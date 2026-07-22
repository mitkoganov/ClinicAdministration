"""Sets/clears the two cookies a session establishes. The single place
cookie flags (HttpOnly, Secure, SameSite, Path) are decided - see
SECURITY.md "Cookie policy" for the documented rationale.
"""

from fastapi import Response

from app.core.config import Settings
from app.core.csrf import CSRF_COOKIE_NAME
from app.core.session_dependency import SESSION_COOKIE_NAME


def set_session_cookies(
    response: Response, raw_session_token: str, raw_csrf_token: str, settings: Settings
) -> None:
    max_age = settings.session_absolute_lifetime_hours * 3600
    # HttpOnly, Secure (outside development), SameSite=Lax, Path=/ - the
    # session token is never readable by JavaScript and never sent
    # cross-site on a top-level navigation. See task.md "Authentication
    # архитектура".
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    # The CSRF cookie is deliberately NOT HttpOnly - the frontend fetch
    # helper must be able to read it and echo it back as the
    # X-CSRF-Token header (the double-submit half of the CSRF defense;
    # see app.core.csrf). Its value is a random token, not a secret an
    # attacker could use without also controlling the session.
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=raw_csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
