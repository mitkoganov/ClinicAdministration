"""CSRF protection for cookie-authenticated mutating requests.

Strategy: a session-bound double-submit token. At login, a random CSRF
token is generated alongside the session token; its hash is stored on the
`AuthSession` row (`csrf_token_hash`) and the raw value is set in a
second, non-`HttpOnly` cookie so frontend JavaScript can read it and echo
it back as a request header. A mutating request must present the same
value in `X-CSRF-Token` that hashes to the session's own stored hash -
tying the token to this specific session server-side is strictly stronger
than a bare double-submit (cookie value equals header value), which alone
is defeatable by an attacker who can set *some* cookie value on the
victim's origin (e.g. via a related subdomain) without ever reading the
real one.

`SameSite=Lax` on the session cookie is a helpful second layer, never the
only one - see task.md "Не разчитай само на SameSite."
"""

from fastapi import Depends, Request

from app.core.errors import AppError
from app.core.session_dependency import get_current_session_optional
from app.core.session_tokens import tokens_match
from app.services.session_service import ValidatedSession

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"

# GET/HEAD/OPTIONS must never have side effects in this API (see task.md)
# - CSRF only ever needs to guard the methods that do.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def require_csrf(
    request: Request,
    validated: ValidatedSession | None = Depends(get_current_session_optional),
) -> None:
    """Add as a dependency on every mutating route reachable via a
    cookie-authenticated session. When there is no valid session at all
    (e.g. the request uses the development-header identity path, or is
    simply unauthenticated), CSRF does not apply here - there is no
    cookie-based session for a forged cross-site request to ride along
    with, and the route's own auth dependency independently rejects an
    unauthenticated caller regardless."""
    if request.method in _SAFE_METHODS:
        return
    if validated is None:
        return

    header_value = request.headers.get(CSRF_HEADER_NAME)
    if not header_value or not tokens_match(header_value, validated.session.csrf_token_hash):
        raise AppError("Missing or invalid CSRF token.", status_code=403)
