"""Development-only identity provider.

This is explicitly NOT an authentication system. It exists solely so the
tenant-context foundation can be built and tested before the real
authentication module exists (see ARCHITECTURE.md, "planned modules").

Safety properties:
* Disabled by default (`DEVELOPMENT_IDENTITY_ENABLED=false`).
* `Settings` refuses to start if this is enabled outside `environment=development`
  (see `app.core.config.Settings._validate_development_identity`).
* The values extracted here are NOT trusted as authorization by themselves —
  `app.core.tenant_context.get_tenant_context` independently re-validates the
  tenant and membership against the database before granting any access.
"""

import uuid

from fastapi import Depends, Header

from app.core.config import Settings, get_settings
from app.core.errors import AppError


class RawIdentity:
    """Unvalidated caller-supplied identity extracted from request headers."""

    def __init__(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        self.user_id = user_id
        self.tenant_id = tenant_id


def get_raw_identity(
    x_dev_user_id: str | None = Header(default=None, alias="X-Dev-User-Id"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    settings: Settings = Depends(get_settings),
) -> RawIdentity:
    identity = _resolve_raw_identity(x_dev_user_id, x_tenant_id, settings)
    if identity is None:
        if not settings.development_identity_enabled:
            raise AppError(
                "No identity provider is configured for this environment.",
                status_code=401,
            )
        raise AppError(
            "Missing required X-Dev-User-Id and/or X-Tenant-Id headers.",
            status_code=401,
        )
    return identity


def try_get_raw_identity(
    x_dev_user_id: str | None, x_tenant_id: str | None, settings: Settings
) -> RawIdentity | None:
    """Same gating and validation as `get_raw_identity`, but returns
    `None` instead of raising when the development identity provider is
    disabled or the headers are absent - used only as the fallback branch
    of `app.core.tenant_context.get_tenant_context`, which tries a real
    production session first and MUST NOT raise merely because no dev
    headers happen to be present either. A malformed (present but
    invalid-UUID) header pair still fails closed with `AppError`, exactly
    as `get_raw_identity` does - "present but garbage" is never silently
    treated as "absent"."""
    return _resolve_raw_identity(x_dev_user_id, x_tenant_id, settings)


def _resolve_raw_identity(
    x_dev_user_id: str | None, x_tenant_id: str | None, settings: Settings
) -> RawIdentity | None:
    if not settings.development_identity_enabled:
        return None
    if not x_dev_user_id or not x_tenant_id:
        return None

    try:
        user_id = uuid.UUID(x_dev_user_id)
        tenant_id = uuid.UUID(x_tenant_id)
    except ValueError as exc:
        raise AppError(
            "X-Dev-User-Id and X-Tenant-Id must be valid UUIDs.",
            status_code=401,
        ) from exc

    return RawIdentity(user_id=user_id, tenant_id=tenant_id)
