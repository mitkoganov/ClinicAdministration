from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.csrf import enforce_csrf_for_valid_session, require_csrf
from app.core.rate_limit import RateLimiter, get_login_rate_limiter
from app.core.session_cookies import clear_session_cookies, set_session_cookies
from app.core.session_dependency import get_current_session, get_current_session_or_none_if_stale
from app.db.session import get_db
from app.models.membership import MembershipStatus
from app.models.tenant import TenantStatus
from app.repositories.membership import MembershipRepository
from app.repositories.tenant import TenantRepository
from app.schemas.auth import (
    ChangePasswordRequest,
    ClinicsResponse,
    ClinicSummary,
    InvitationAcceptRequest,
    LoginRequest,
    MeResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequestRequest,
    SelectClinicRequest,
    StatusResponse,
)
from app.services.auth_service import AuthService
from app.services.invitation_service import InvitationService
from app.services.password_reset_service import PasswordResetService
from app.services.session_service import SessionService, ValidatedSession, effective_session_expiry

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Mutation routes that already require a session (change-password,
# select-clinic) depend on `require_csrf` - the session itself is
# resolved a second time inside `require_csrf` via
# `get_current_session_optional`, matching the established convention that
# CSRF checking and the route's own auth dependency are independent
# concerns (see app.core.csrf). Login and the anonymous password-reset/
# invitation-acceptance routes have no session yet, so CSRF does not apply
# to them (see app.core.csrf.require_csrf's own docstring). Logout is a
# deliberate exception to this pattern - see its own docstring below.


@router.post("/login", response_model=StatusResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    rate_limiter: RateLimiter = Depends(get_login_rate_limiter),
) -> StatusResponse:
    client_ip = request.client.host if request.client else None
    created = AuthService(db, settings, rate_limiter).login(
        payload.email, payload.password, client_ip
    )
    set_session_cookies(response, created.raw_token, created.raw_csrf_token, settings)
    return StatusResponse()


@router.post("/logout", response_model=StatusResponse)
def logout(
    request: Request,
    response: Response,
    validated: ValidatedSession | None = Depends(get_current_session_or_none_if_stale),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StatusResponse:
    """Deliberately does NOT use the shared `require_csrf` dependency:
    that depends on `get_current_session_optional`, which re-raises
    `InvalidSessionError` for a stale cookie (correct everywhere else,
    see app.core.session_dependency) - here it would turn a stale-cookie
    logout into a 401 instead of the idempotent success below. Logout
    resolves its session via `get_current_session_or_none_if_stale`
    instead, which treats "no usable session" (missing, unknown, expired,
    revoked, inactive-account) uniformly as `None` - clearing cookies and
    returning 200 without ever revealing which condition applied, and
    without requiring CSRF for a call that revokes nothing. CSRF is still
    enforced (`enforce_csrf_for_valid_session`) whenever there IS a
    genuinely valid session to revoke - a missing/invalid CSRF token
    there is a controlled 403 that revokes nothing and clears nothing."""
    if validated is not None:
        enforce_csrf_for_valid_session(request, validated)
        AuthService(db, settings, None).logout(validated.session, validated.user.id)
    clear_session_cookies(response)
    return StatusResponse()


@router.get("/me", response_model=MeResponse)
def me(
    validated: ValidatedSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> MeResponse:
    selected_clinic = None
    role = None
    tenant_id = validated.session.selected_tenant_id
    if tenant_id is not None:
        tenant = TenantRepository(db).get_by_id(tenant_id)
        membership = MembershipRepository(db).get_membership(tenant_id, validated.user.id)
        # A selected tenant/membership that has since gone inactive (or
        # disappeared) is silently omitted here, not an error - task.md
        # "selected clinic, ако е валидна."
        if tenant is not None and membership is not None:
            if (
                tenant.status == TenantStatus.ACTIVE
                and membership.status == MembershipStatus.ACTIVE
            ):
                selected_clinic = ClinicSummary(
                    tenant_id=tenant.id, name=tenant.name, role=membership.role
                )
                role = membership.role

    return MeResponse(
        user_id=validated.user.id,
        email=validated.user.normalized_email,
        display_name=validated.user.display_name,
        selected_clinic=selected_clinic,
        role=role,
        session_expires_at=effective_session_expiry(validated.session),
    )


@router.get("/clinics", response_model=ClinicsResponse)
def list_clinics(
    validated: ValidatedSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ClinicsResponse:
    pairs = MembershipRepository(db).list_active_for_user(validated.user.id)
    return ClinicsResponse(
        items=[
            ClinicSummary(tenant_id=tenant.id, name=tenant.name, role=membership.role)
            for tenant, membership in pairs
        ]
    )


@router.post("/select-clinic", response_model=StatusResponse, dependencies=[Depends(require_csrf)])
def select_clinic(
    payload: SelectClinicRequest,
    validated: ValidatedSession = Depends(get_current_session),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StatusResponse:
    SessionService(db, settings).select_clinic(
        validated.session, validated.user.id, payload.tenant_id
    )
    return StatusResponse()


@router.post(
    "/change-password", response_model=StatusResponse, dependencies=[Depends(require_csrf)]
)
def change_password(
    payload: ChangePasswordRequest,
    validated: ValidatedSession = Depends(get_current_session),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StatusResponse:
    AuthService(db, settings, None).change_password(
        validated.user, validated.session, payload.current_password, payload.new_password
    )
    return StatusResponse()


@router.post("/password-reset/request", response_model=StatusResponse)
def request_password_reset(
    payload: PasswordResetRequestRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StatusResponse:
    # The return value is intentionally discarded here - the raw token
    # must never reach the production API response (see task.md).
    PasswordResetService(db, settings).request_reset(payload.email)
    return StatusResponse()


@router.post("/password-reset/confirm", response_model=StatusResponse)
def confirm_password_reset(
    payload: PasswordResetConfirmRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StatusResponse:
    PasswordResetService(db, settings).confirm_reset(payload.token, payload.new_password)
    return StatusResponse()


@router.post("/invitations/accept", response_model=StatusResponse)
def accept_invitation(
    payload: InvitationAcceptRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StatusResponse:
    # InvitationService.accept_invitation owns the single commit for the
    # whole attempt (account, membership, session, token consumption) -
    # this route performs no commit of its own, and only sets cookies once
    # that commit has actually succeeded.
    result = InvitationService(db, settings).accept_invitation(
        payload.token, payload.display_name, payload.password, SessionService(db, settings)
    )
    set_session_cookies(response, result.session.raw_token, result.session.raw_csrf_token, settings)
    return StatusResponse()
