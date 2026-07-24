"""Import every ORM model module here so Alembic autogenerate (which only
walks `Base.metadata`) picks up new tables. `app.db.base` imports this
package for the same reason."""

from app.models.auth_session import AuthSession
from app.models.membership import MembershipRole, MembershipStatus, TenantMembership
from app.models.one_time_token import OneTimeToken, TokenPurpose
from app.models.tenant import Tenant, TenantStatus
from app.models.tenant_scoped_record import TenantScopedRecord
from app.models.user_account import EmailVerificationState, UserAccount, UserAccountStatus

__all__ = [
    "AuthSession",
    "EmailVerificationState",
    "MembershipRole",
    "MembershipStatus",
    "OneTimeToken",
    "Tenant",
    "TenantMembership",
    "TenantScopedRecord",
    "TenantStatus",
    "TokenPurpose",
    "UserAccount",
    "UserAccountStatus",
]
