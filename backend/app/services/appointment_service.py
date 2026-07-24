import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import AuditEvent, AuditOutcome, emit_audit_event
from app.core.authorization import (
    CALENDAR_OVERRIDE_ROLES,
    CALENDAR_READ_ROLES,
    CALENDAR_WRITE_ROLES,
    require_role,
)
from app.core.errors import CalendarConflictError, ConflictError, ForbiddenError, NotFoundError
from app.core.tenant_context import TenantContext
from app.models.appointment import Appointment, AppointmentStatus
from app.models.appointment_service_type import ServiceTypeStatus
from app.models.clinic_room import ClinicRoomStatus
from app.models.membership import MembershipStatus
from app.repositories.appointment import AppointmentRepository
from app.repositories.appointment_service_type import AppointmentServiceTypeRepository
from app.repositories.clinic_room import ClinicRoomRepository
from app.repositories.membership import MembershipRepository
from app.repositories.tenant import TenantRepository
from app.services.availability_service import AvailabilityService

_RESOURCE_TYPE = "appointment"

MAX_BOOKING_HORIZON_DAYS = 365

# Human-readable message per task.md's required machine-readable `409`
# conflict code (see AvailabilityService.diagnose_unavailable_reason for
# how the code itself is chosen).
_UNAVAILABILITY_MESSAGES: dict[str, str] = {
    "appointment_conflict": "This time conflicts with another appointment.",
    "room_unavailable": "The room is inactive or blocked for the requested time.",
    "blocked_period": "The provider is blocked (leave/training/etc.) for the requested time.",
    "outside_schedule": "The provider has no schedule covering the requested time.",
    "provider_unavailable": (
        "The requested time is outside the provider's working hours or inside a break."
    ),
}


def _unavailability_error(code: str) -> CalendarConflictError:
    return CalendarConflictError(
        _UNAVAILABILITY_MESSAGES.get(code, "The requested time is not available."), code=code
    )


_PROVIDER_OVERLAP_CONSTRAINT = "ex_appointments_provider_overlap"
_ROOM_OVERLAP_CONSTRAINT = "ex_appointments_room_overlap"

_ALLOWED_TRANSITIONS: dict[AppointmentStatus, frozenset[AppointmentStatus]] = {
    AppointmentStatus.SCHEDULED: frozenset(
        {
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.CANCELLED,
            AppointmentStatus.COMPLETED,
            AppointmentStatus.NO_SHOW,
        }
    ),
    AppointmentStatus.CONFIRMED: frozenset(
        {AppointmentStatus.CANCELLED, AppointmentStatus.COMPLETED, AppointmentStatus.NO_SHOW}
    ),
    AppointmentStatus.CANCELLED: frozenset(),
    AppointmentStatus.COMPLETED: frozenset(),
    AppointmentStatus.NO_SHOW: frozenset(),
}

_RESCHEDULABLE_STATUSES = frozenset({AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED})


def _overlap_constraint_name(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


class AppointmentService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = AppointmentRepository(db)
        self._service_types = AppointmentServiceTypeRepository(db)
        self._rooms = ClinicRoomRepository(db)
        self._memberships = MembershipRepository(db)
        self._tenants = TenantRepository(db)
        self._availability = AvailabilityService(db)

    # --- Reads --------------------------------------------------------

    def get(self, context: TenantContext, appointment_id: uuid.UUID) -> Appointment:
        appointment = self._repo.get_by_id(context.tenant_id, appointment_id)
        if appointment is None:
            raise NotFoundError()
        if (
            context.role not in CALENDAR_READ_ROLES
            and appointment.provider_user_id != context.user_id
        ):
            raise NotFoundError()
        return appointment

    def list(
        self,
        context: TenantContext,
        *,
        range_start: datetime | None,
        range_end: datetime | None,
        provider_user_id: uuid.UUID | None,
        room_id: uuid.UUID | None,
        service_type_id: uuid.UUID | None,
        status: AppointmentStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Appointment], int]:
        # Any active member may see their OWN calendar; only
        # CALENDAR_READ_ROLES may see other providers' appointments -
        # enforced by silently narrowing the filter, never a 403, so a
        # provider's own "/calendar" view works regardless of role.
        effective_provider_id = provider_user_id
        if context.role not in CALENDAR_READ_ROLES:
            effective_provider_id = context.user_id
        return self._repo.list_by_tenant(
            context.tenant_id,
            range_start=range_start,
            range_end=range_end,
            provider_user_id=effective_provider_id,
            room_id=room_id,
            service_type_id=service_type_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    def update_metadata(
        self,
        context: TenantContext,
        appointment_id: uuid.UUID,
        expected_version: int,
        *,
        patient_display_name: str | None = None,
        patient_phone: str | None = None,
        patient_email: str | None = None,
        notes: str | None = None,
    ) -> Appointment:
        """Non-status metadata only (patient contact snapshot, notes) -
        status transitions never go through here, only through the
        explicit action methods above (see
        app.schemas.calendar.AppointmentMetadataUpdate's docstring).

        Deliberately NO self-scoped bypass: task.md's authorization
        matrix grants a self-scoped bypass only for `complete`/`no_show`
        (see `_transition`'s `allow_self_scoped` parameter) - being the
        appointment's own provider does not, by itself, grant the right
        to edit the patient contact snapshot or notes. Every caller,
        including the provider, needs `CALENDAR_WRITE_ROLES`."""
        existing = self._repo.get_by_id(context.tenant_id, appointment_id)
        if existing is None:
            self._audit(
                context, "appointment.metadata_updated", AuditOutcome.REJECTED, appointment_id
            )
            raise NotFoundError()

        try:
            require_role(context, CALENDAR_WRITE_ROLES)
        except ForbiddenError:
            self._audit(
                context, "appointment.metadata_updated", AuditOutcome.REJECTED, appointment_id
            )
            raise

        values = {
            k: v
            for k, v in {
                "patient_display_name": patient_display_name,
                "patient_phone": patient_phone,
                "patient_email": patient_email,
                "notes": notes,
            }.items()
            if v is not None
        }
        updated = self._repo.update_with_version(
            context.tenant_id,
            appointment_id,
            expected_version,
            updated_by_user_id=context.user_id,
            values=values,
        )
        if updated is None:
            self._audit(
                context, "appointment.metadata_updated", AuditOutcome.REJECTED, appointment_id
            )
            raise CalendarConflictError(
                "This appointment was modified by someone else. Reload and try again.",
                code="stale_version",
            )

        self._db.commit()
        self._audit(context, "appointment.metadata_updated", AuditOutcome.SUCCESS, updated.id)
        return updated

    # --- Create ---------------------------------------------------------

    def create(
        self,
        context: TenantContext,
        *,
        provider_user_id: uuid.UUID,
        room_id: uuid.UUID | None,
        service_type_id: uuid.UUID,
        starts_at: datetime,
        ends_at: datetime,
        patient_display_name: str,
        patient_phone: str | None,
        patient_email: str | None,
        notes: str | None,
        override_availability: bool = False,
        override_reason: str | None = None,
    ) -> Appointment:
        try:
            require_role(context, CALENDAR_WRITE_ROLES)
            if override_availability:
                require_role(context, CALENDAR_OVERRIDE_ROLES)
                if not override_reason or not override_reason.strip():
                    raise ConflictError("override_reason is required when overriding availability.")

            now = datetime.now(UTC)
            if starts_at >= ends_at:
                raise ConflictError("starts_at must be before ends_at.")
            if starts_at < now:
                raise ConflictError("Cannot create an appointment in the past.")
            if starts_at > now + timedelta(days=MAX_BOOKING_HORIZON_DAYS):
                raise ConflictError(
                    f"Cannot book more than {MAX_BOOKING_HORIZON_DAYS} days in advance."
                )

            tenant = self._tenants.get_by_id(context.tenant_id)
            if tenant is None:
                raise NotFoundError()

            membership = self._memberships.get_membership(context.tenant_id, provider_user_id)
            if membership is None or membership.status != MembershipStatus.ACTIVE:
                raise NotFoundError("Provider not found or not active in this clinic.")

            service_type = self._service_types.get_by_id(context.tenant_id, service_type_id)
            if service_type is None:
                raise NotFoundError("Service type not found in this clinic.")
            if service_type.status != ServiceTypeStatus.ACTIVE:
                raise ConflictError("Service type is not active.")

            if room_id is not None:
                room = self._rooms.get_by_id(context.tenant_id, room_id)
                if room is None:
                    raise NotFoundError("Room not found in this clinic.")
                if room.status != ClinicRoomStatus.ACTIVE:
                    raise _unavailability_error("room_unavailable")

            if not override_availability:
                expected_duration = timedelta(minutes=service_type.default_duration_minutes)
                if (ends_at - starts_at) != expected_duration:
                    raise ConflictError(
                        "Appointment duration must match the service type's duration."
                    )
                buffer_before = timedelta(minutes=service_type.buffer_before_minutes)
                buffer_after = timedelta(minutes=service_type.buffer_after_minutes)
                available = self._availability.is_interval_free(
                    context.tenant_id,
                    tenant.timezone,
                    provider_user_id=provider_user_id,
                    room_id=room_id,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    buffer_before=buffer_before,
                    buffer_after=buffer_after,
                )
                if not available:
                    reason = self._availability.diagnose_unavailable_reason(
                        context.tenant_id,
                        tenant.timezone,
                        provider_user_id=provider_user_id,
                        room_id=room_id,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        buffer_before=buffer_before,
                        buffer_after=buffer_after,
                    )
                    raise _unavailability_error(reason)

            if not patient_display_name or not patient_display_name.strip():
                raise ConflictError("patient_display_name is required.")
        except (ForbiddenError, ConflictError, NotFoundError):
            self._audit(context, "appointment.created", AuditOutcome.REJECTED)
            raise

        try:
            appointment = self._repo.create(
                context.tenant_id,
                provider_user_id=provider_user_id,
                room_id=room_id,
                service_type_id=service_type_id,
                starts_at=starts_at,
                ends_at=ends_at,
                patient_display_name=patient_display_name.strip(),
                patient_phone=patient_phone,
                patient_email=patient_email,
                notes=notes,
                created_by_user_id=context.user_id,
            )
        except IntegrityError as exc:
            self._db.rollback()
            constraint = _overlap_constraint_name(exc)
            if constraint not in (_PROVIDER_OVERLAP_CONSTRAINT, _ROOM_OVERLAP_CONSTRAINT):
                raise
            self._audit(context, "appointment.created", AuditOutcome.REJECTED)
            raise CalendarConflictError(
                "This time conflicts with another appointment.", code="appointment_conflict"
            ) from exc

        self._db.commit()
        if override_availability:
            self._audit(context, "appointment.override_used", AuditOutcome.SUCCESS, appointment.id)
        self._audit(context, "appointment.created", AuditOutcome.SUCCESS, appointment.id)
        return appointment

    # --- Reschedule -----------------------------------------------------

    def reschedule(
        self,
        context: TenantContext,
        appointment_id: uuid.UUID,
        expected_version: int,
        *,
        starts_at: datetime,
        ends_at: datetime,
        provider_user_id: uuid.UUID | None = None,
        room_id: uuid.UUID | None = None,
        override_availability: bool = False,
        override_reason: str | None = None,
    ) -> Appointment:
        try:
            require_role(context, CALENDAR_WRITE_ROLES)
            if override_availability:
                require_role(context, CALENDAR_OVERRIDE_ROLES)
                if not override_reason or not override_reason.strip():
                    raise ConflictError("override_reason is required when overriding availability.")
        except ForbiddenError:
            self._audit(context, "appointment.rescheduled", AuditOutcome.REJECTED, appointment_id)
            raise

        existing = self._repo.get_by_id(context.tenant_id, appointment_id)
        if existing is None:
            self._audit(context, "appointment.rescheduled", AuditOutcome.REJECTED, appointment_id)
            raise NotFoundError()

        try:
            if existing.status not in _RESCHEDULABLE_STATUSES:
                raise CalendarConflictError(
                    f"Cannot reschedule an appointment in status '{existing.status.value}'.",
                    code="invalid_status_transition",
                )
            if starts_at >= ends_at:
                raise ConflictError("starts_at must be before ends_at.")

            now = datetime.now(UTC)
            if starts_at < now:
                raise ConflictError("Cannot reschedule an appointment into the past.")

            new_provider = provider_user_id or existing.provider_user_id
            new_room = room_id if room_id is not None else existing.room_id

            membership = self._memberships.get_membership(context.tenant_id, new_provider)
            if membership is None or membership.status != MembershipStatus.ACTIVE:
                raise NotFoundError("Provider not found or not active in this clinic.")

            service_type = self._service_types.get_by_id(
                context.tenant_id, existing.service_type_id
            )
            if service_type is None:
                raise NotFoundError("Service type not found in this clinic.")

            if new_room is not None:
                room = self._rooms.get_by_id(context.tenant_id, new_room)
                if room is None:
                    raise NotFoundError("Room not found in this clinic.")
                if room.status != ClinicRoomStatus.ACTIVE:
                    raise _unavailability_error("room_unavailable")

            tenant = self._tenants.get_by_id(context.tenant_id)
            if tenant is None:
                raise NotFoundError()

            if not override_availability:
                buffer_before = timedelta(minutes=service_type.buffer_before_minutes)
                buffer_after = timedelta(minutes=service_type.buffer_after_minutes)
                available = self._availability.is_interval_free(
                    context.tenant_id,
                    tenant.timezone,
                    provider_user_id=new_provider,
                    room_id=new_room,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    buffer_before=buffer_before,
                    buffer_after=buffer_after,
                    exclude_appointment_id=existing.id,
                )
                if not available:
                    reason = self._availability.diagnose_unavailable_reason(
                        context.tenant_id,
                        tenant.timezone,
                        provider_user_id=new_provider,
                        room_id=new_room,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        buffer_before=buffer_before,
                        buffer_after=buffer_after,
                        exclude_appointment_id=existing.id,
                    )
                    raise _unavailability_error(reason)
        except (ForbiddenError, ConflictError, NotFoundError):
            self._audit(context, "appointment.rescheduled", AuditOutcome.REJECTED, appointment_id)
            raise

        try:
            updated = self._repo.update_with_version(
                context.tenant_id,
                appointment_id,
                expected_version,
                updated_by_user_id=context.user_id,
                values={
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "provider_user_id": new_provider,
                    "room_id": new_room,
                },
            )
        except IntegrityError as exc:
            self._db.rollback()
            constraint = _overlap_constraint_name(exc)
            if constraint not in (_PROVIDER_OVERLAP_CONSTRAINT, _ROOM_OVERLAP_CONSTRAINT):
                raise
            self._audit(context, "appointment.rescheduled", AuditOutcome.REJECTED, appointment_id)
            raise CalendarConflictError(
                "This time conflicts with another appointment.", code="appointment_conflict"
            ) from exc

        if updated is None:
            self._audit(context, "appointment.rescheduled", AuditOutcome.REJECTED, appointment_id)
            raise CalendarConflictError(
                "This appointment was modified by someone else. Reload and try again.",
                code="stale_version",
            )

        self._db.commit()
        if override_availability:
            self._audit(context, "appointment.override_used", AuditOutcome.SUCCESS, updated.id)
        self._audit(context, "appointment.rescheduled", AuditOutcome.SUCCESS, updated.id)
        return updated

    # --- Lifecycle actions -----------------------------------------------

    def cancel(
        self,
        context: TenantContext,
        appointment_id: uuid.UUID,
        expected_version: int,
        *,
        reason: str,
    ) -> Appointment:
        # Idempotent: cancelling an already-cancelled appointment returns
        # its current state rather than a 409 invalid_status_transition -
        # a caller retrying a cancel (e.g. after a lost response) must not
        # see this as an error, and this must never emit a second SUCCESS
        # audit for a mutation that didn't actually happen again.
        existing = self._repo.get_by_id(context.tenant_id, appointment_id)
        if existing is not None and existing.status == AppointmentStatus.CANCELLED:
            if existing.provider_user_id != context.user_id:
                require_role(context, CALENDAR_WRITE_ROLES)
            return existing
        return self._transition(
            context,
            appointment_id,
            expected_version,
            target_status=AppointmentStatus.CANCELLED,
            event_type="appointment.cancelled",
            extra_values={"cancellation_reason": reason, "cancelled_at": datetime.now(UTC)},
            allow_self_scoped=False,
            require_not_before_start=False,
        )

    def confirm(
        self, context: TenantContext, appointment_id: uuid.UUID, expected_version: int
    ) -> Appointment:
        return self._transition(
            context,
            appointment_id,
            expected_version,
            target_status=AppointmentStatus.CONFIRMED,
            event_type="appointment.confirmed",
            extra_values={},
            allow_self_scoped=False,
            require_not_before_start=False,
        )

    def complete(
        self, context: TenantContext, appointment_id: uuid.UUID, expected_version: int
    ) -> Appointment:
        return self._transition(
            context,
            appointment_id,
            expected_version,
            target_status=AppointmentStatus.COMPLETED,
            event_type="appointment.completed",
            extra_values={},
            allow_self_scoped=True,
            require_not_before_start=True,
        )

    def no_show(
        self, context: TenantContext, appointment_id: uuid.UUID, expected_version: int
    ) -> Appointment:
        return self._transition(
            context,
            appointment_id,
            expected_version,
            target_status=AppointmentStatus.NO_SHOW,
            event_type="appointment.no_show",
            extra_values={},
            allow_self_scoped=True,
            require_not_before_start=True,
        )

    def _transition(
        self,
        context: TenantContext,
        appointment_id: uuid.UUID,
        expected_version: int,
        *,
        target_status: AppointmentStatus,
        event_type: str,
        extra_values: dict[str, object],
        allow_self_scoped: bool,
        require_not_before_start: bool,
    ) -> Appointment:
        existing = self._repo.get_by_id(context.tenant_id, appointment_id)
        if existing is None:
            self._audit(context, event_type, AuditOutcome.REJECTED, appointment_id)
            raise NotFoundError()

        is_self_scoped_actor = allow_self_scoped and existing.provider_user_id == context.user_id
        try:
            if not is_self_scoped_actor:
                require_role(context, CALENDAR_WRITE_ROLES)

            if target_status not in _ALLOWED_TRANSITIONS.get(existing.status, frozenset()):
                raise CalendarConflictError(
                    f"Cannot transition an appointment from '{existing.status.value}' to "
                    f"'{target_status.value}'.",
                    code="invalid_status_transition",
                )
            if require_not_before_start and datetime.now(UTC) < existing.starts_at:
                raise ConflictError(
                    "Cannot mark an appointment completed/no-show before its start time."
                )
        except (ForbiddenError, ConflictError, NotFoundError):
            self._audit(context, event_type, AuditOutcome.REJECTED, appointment_id)
            raise

        updated = self._repo.update_with_version(
            context.tenant_id,
            appointment_id,
            expected_version,
            updated_by_user_id=context.user_id,
            values={"status": target_status, **extra_values},
        )
        if updated is None:
            self._audit(context, event_type, AuditOutcome.REJECTED, appointment_id)
            raise CalendarConflictError(
                "This appointment was modified by someone else. Reload and try again.",
                code="stale_version",
            )

        self._db.commit()
        self._audit(context, event_type, AuditOutcome.SUCCESS, updated.id)
        return updated

    def _audit(
        self,
        context: TenantContext,
        event_type: str,
        outcome: AuditOutcome,
        resource_id: uuid.UUID | None = None,
    ) -> None:
        emit_audit_event(
            AuditEvent(
                event_type=event_type,
                actor_user_id=context.user_id if context is not None else None,
                target_resource_type=_RESOURCE_TYPE,
                outcome=outcome,
                tenant_id=context.tenant_id if context is not None else None,
                target_resource_id=resource_id,
            )
        )
