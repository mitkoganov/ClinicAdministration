import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base application error. Carries a safe, user-facing message only —
    never include secrets or internal exception details here."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    """Requested resource does not exist within the caller's validated scope.

    Also raised for cross-tenant access attempts, deliberately: a resource
    that exists but belongs to another tenant must be indistinguishable from
    one that does not exist at all, to avoid leaking foreign-tenant
    existence (IDOR/enumeration prevention)."""

    def __init__(self, message: str = "Not found") -> None:
        super().__init__(message, status_code=404)


class ForbiddenError(AppError):
    """Caller is authenticated and tenant-scoped but lacks the role required
    for this action."""

    def __init__(self, message: str = "Forbidden") -> None:
        super().__init__(message, status_code=403)


class ConflictError(AppError):
    """The request is well-formed and the caller is authorized, but the
    action would violate a business invariant (e.g. a duplicate membership,
    or removing the clinic's last active owner)."""

    def __init__(self, message: str = "Conflict") -> None:
        super().__init__(message, status_code=409)


class UnauthorizedError(AppError):
    """Caller has no valid, current authentication (missing/invalid/
    expired/revoked session, or a login attempt that failed). Deliberately
    generic - never distinguishes "no such account" from "wrong password"
    from "account exists but is inactive" (see app.services.auth_service)."""

    def __init__(self, message: str = "Authentication required.") -> None:
        super().__init__(message, status_code=401)


class RateLimitedError(AppError):
    """Caller has exceeded a bounded rate limit (see app.core.rate_limit).
    Never reveals whether the underlying account exists."""

    def __init__(self, message: str = "Too many attempts. Please try again later.") -> None:
        super().__init__(message, status_code=429)


class InvalidTokenError(AppError):
    """A one-time token (password reset / invitation acceptance) is
    missing, malformed, expired, already consumed, or revoked. Always the
    same generic message regardless of which specific condition applied -
    never reveals which."""

    def __init__(self, message: str = "This link is invalid or has expired.") -> None:
        super().__init__(message, status_code=400)


class WeakPasswordError(AppError):
    """A submitted password fails the documented policy (see
    app.core.passwords). Carries the specific policy-violation message -
    this is a validation error, not a secret, so it is safe to return."""

    def __init__(self, message: str = "Password does not meet the required policy.") -> None:
        super().__init__(message, status_code=422)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error")
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
