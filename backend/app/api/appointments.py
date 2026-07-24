import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.authorization import CALENDAR_CONTACT_VISIBLE_ROLES
from app.core.csrf import require_csrf
from app.core.tenant_context import TenantContext, get_tenant_context
from app.db.session import get_db
from app.models.appointment import Appointment, AppointmentStatus
from app.schemas.calendar import (
    AppointmentCancelRequest,
    AppointmentCreate,
    AppointmentList,
    AppointmentMetadataUpdate,
    AppointmentRead,
    AppointmentRescheduleRequest,
    AppointmentSummaryRead,
    AppointmentVersionedActionRequest,
)
from app.services.appointment_service import AppointmentService

router = APIRouter(prefix="/api/v1/appointments", tags=["appointments"])

_AppointmentResponse = AppointmentRead | AppointmentSummaryRead


def _serialize(context: TenantContext, appointment: Appointment) -> _AppointmentResponse:
    """The single place every appointment response (read, list, create,
    metadata update, reschedule, and every lifecycle action) goes
    through - patient contact visibility (`patient_phone`/
    `patient_email`) depends ONLY on `CALENDAR_CONTACT_VISIBLE_ROLES`,
    never on whether the caller happens to be the appointment's own
    provider. "Provider" is a scheduling fact, not a permission grant -
    task.md's authorization matrix draws that self-scoped exception
    narrowly (view-own-calendar, complete/no-show), and never extends it
    to the patient contact snapshot. No route may call
    `AppointmentRead.model_validate(...)` directly - doing so would
    bypass this policy for whichever endpoint did it."""
    if context.role in CALENDAR_CONTACT_VISIBLE_ROLES:
        return AppointmentRead.model_validate(appointment)
    return AppointmentSummaryRead.model_validate(appointment)


@router.get("", response_model=AppointmentList)
def list_appointments(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    provider_id: uuid.UUID | None = Query(default=None),
    room_id: uuid.UUID | None = Query(default=None),
    service_type_id: uuid.UUID | None = Query(default=None),
    status_filter: AppointmentStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> AppointmentList:
    items, total = AppointmentService(db).list(
        context,
        range_start=date_from,
        range_end=date_to,
        provider_user_id=provider_id,
        room_id=room_id,
        service_type_id=service_type_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return AppointmentList(
        items=[_serialize(context, item) for item in items], total=total, limit=limit, offset=offset
    )


@router.post("", response_model=_AppointmentResponse, dependencies=[Depends(require_csrf)])
def create_appointment(
    payload: AppointmentCreate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> _AppointmentResponse:
    appointment = AppointmentService(db).create(
        context,
        provider_user_id=payload.provider_user_id,
        room_id=payload.room_id,
        service_type_id=payload.service_type_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        patient_display_name=payload.patient_display_name,
        patient_phone=payload.patient_phone,
        patient_email=payload.patient_email,
        notes=payload.notes,
        override_availability=payload.override_availability,
        override_reason=payload.override_reason,
    )
    return _serialize(context, appointment)


@router.get("/{appointment_id}", response_model=_AppointmentResponse)
def get_appointment(
    appointment_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> _AppointmentResponse:
    appointment = AppointmentService(db).get(context, appointment_id)
    return _serialize(context, appointment)


@router.patch(
    "/{appointment_id}", response_model=_AppointmentResponse, dependencies=[Depends(require_csrf)]
)
def update_appointment_metadata(
    appointment_id: uuid.UUID,
    payload: AppointmentMetadataUpdate,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> _AppointmentResponse:
    fields_set = payload.model_fields_set - {"expected_version"}
    updated = AppointmentService(db).update_metadata(
        context,
        appointment_id,
        payload.expected_version,
        fields_set=fields_set,
        patient_display_name=payload.patient_display_name,
        patient_phone=payload.patient_phone,
        patient_email=payload.patient_email,
        notes=payload.notes,
        room_id=payload.room_id,
    )
    return _serialize(context, updated)


@router.post(
    "/{appointment_id}/reschedule",
    response_model=_AppointmentResponse,
    dependencies=[Depends(require_csrf)],
)
def reschedule_appointment(
    appointment_id: uuid.UUID,
    payload: AppointmentRescheduleRequest,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> _AppointmentResponse:
    appointment = AppointmentService(db).reschedule(
        context,
        appointment_id,
        payload.expected_version,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        provider_user_id=payload.provider_user_id,
        room_id=payload.room_id,
        override_availability=payload.override_availability,
        override_reason=payload.override_reason,
    )
    return _serialize(context, appointment)


@router.post(
    "/{appointment_id}/cancel",
    response_model=_AppointmentResponse,
    dependencies=[Depends(require_csrf)],
)
def cancel_appointment(
    appointment_id: uuid.UUID,
    payload: AppointmentCancelRequest,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> _AppointmentResponse:
    appointment = AppointmentService(db).cancel(
        context, appointment_id, payload.expected_version, reason=payload.reason
    )
    return _serialize(context, appointment)


@router.post(
    "/{appointment_id}/confirm",
    response_model=_AppointmentResponse,
    dependencies=[Depends(require_csrf)],
)
def confirm_appointment(
    appointment_id: uuid.UUID,
    payload: AppointmentVersionedActionRequest,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> _AppointmentResponse:
    appointment = AppointmentService(db).confirm(context, appointment_id, payload.expected_version)
    return _serialize(context, appointment)


@router.post(
    "/{appointment_id}/complete",
    response_model=_AppointmentResponse,
    dependencies=[Depends(require_csrf)],
)
def complete_appointment(
    appointment_id: uuid.UUID,
    payload: AppointmentVersionedActionRequest,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> _AppointmentResponse:
    appointment = AppointmentService(db).complete(context, appointment_id, payload.expected_version)
    return _serialize(context, appointment)


@router.post(
    "/{appointment_id}/no-show",
    response_model=_AppointmentResponse,
    dependencies=[Depends(require_csrf)],
)
def mark_appointment_no_show(
    appointment_id: uuid.UUID,
    payload: AppointmentVersionedActionRequest,
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> _AppointmentResponse:
    appointment = AppointmentService(db).no_show(context, appointment_id, payload.expected_version)
    return _serialize(context, appointment)
