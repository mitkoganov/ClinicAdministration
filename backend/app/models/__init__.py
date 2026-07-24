"""Import every ORM model module here so Alembic autogenerate (which only
walks `Base.metadata`) picks up new tables. `app.db.base` imports this
package for the same reason."""

from app.models.appointment import Appointment, AppointmentStatus
from app.models.appointment_service_type import AppointmentServiceType, ServiceTypeStatus
from app.models.auth_session import AuthSession
from app.models.calendar_block import CalendarBlock, CalendarBlockType
from app.models.clinic_room import ClinicRoom, ClinicRoomStatus
from app.models.membership import MembershipRole, MembershipStatus, TenantMembership
from app.models.one_time_token import OneTimeToken, TokenPurpose
from app.models.provider_schedule import ProviderSchedule, ProviderScheduleStatus, ScheduleBreak
from app.models.tenant import Tenant, TenantStatus
from app.models.tenant_scoped_record import TenantScopedRecord
from app.models.user_account import EmailVerificationState, UserAccount, UserAccountStatus

__all__ = [
    "Appointment",
    "AppointmentServiceType",
    "AppointmentStatus",
    "AuthSession",
    "CalendarBlock",
    "CalendarBlockType",
    "ClinicRoom",
    "ClinicRoomStatus",
    "EmailVerificationState",
    "MembershipRole",
    "MembershipStatus",
    "OneTimeToken",
    "ProviderSchedule",
    "ProviderScheduleStatus",
    "ScheduleBreak",
    "ServiceTypeStatus",
    "Tenant",
    "TenantMembership",
    "TenantScopedRecord",
    "TenantStatus",
    "TokenPurpose",
    "UserAccount",
    "UserAccountStatus",
]
