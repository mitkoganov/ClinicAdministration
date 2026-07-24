import pytest

from app.models.appointment import AppointmentStatus, normalize_patient_phone
from app.services.appointment_service import _ALLOWED_TRANSITIONS, _RESCHEDULABLE_STATUSES


@pytest.mark.parametrize(
    ("from_status", "to_status", "allowed"),
    [
        (AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED, True),
        (AppointmentStatus.SCHEDULED, AppointmentStatus.CANCELLED, True),
        (AppointmentStatus.SCHEDULED, AppointmentStatus.COMPLETED, True),
        (AppointmentStatus.SCHEDULED, AppointmentStatus.NO_SHOW, True),
        (AppointmentStatus.CONFIRMED, AppointmentStatus.CANCELLED, True),
        (AppointmentStatus.CONFIRMED, AppointmentStatus.COMPLETED, True),
        (AppointmentStatus.CONFIRMED, AppointmentStatus.NO_SHOW, True),
        (AppointmentStatus.CONFIRMED, AppointmentStatus.SCHEDULED, False),
        (AppointmentStatus.CANCELLED, AppointmentStatus.SCHEDULED, False),
        (AppointmentStatus.CANCELLED, AppointmentStatus.CONFIRMED, False),
        (AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED, False),
        (AppointmentStatus.COMPLETED, AppointmentStatus.SCHEDULED, False),
        (AppointmentStatus.NO_SHOW, AppointmentStatus.SCHEDULED, False),
        (AppointmentStatus.NO_SHOW, AppointmentStatus.COMPLETED, False),
    ],
)
def test_status_transition_matrix(from_status, to_status, allowed):
    assert (to_status in _ALLOWED_TRANSITIONS.get(from_status, frozenset())) == allowed


def test_terminal_statuses_have_no_outgoing_transitions():
    for terminal in (
        AppointmentStatus.CANCELLED,
        AppointmentStatus.COMPLETED,
        AppointmentStatus.NO_SHOW,
    ):
        assert _ALLOWED_TRANSITIONS[terminal] == frozenset()


@pytest.mark.parametrize(
    ("status", "reschedulable"),
    [
        (AppointmentStatus.SCHEDULED, True),
        (AppointmentStatus.CONFIRMED, True),
        (AppointmentStatus.CANCELLED, False),
        (AppointmentStatus.COMPLETED, False),
        (AppointmentStatus.NO_SHOW, False),
    ],
)
def test_reschedulable_statuses(status, reschedulable):
    assert (status in _RESCHEDULABLE_STATUSES) == reschedulable


# --- Patient phone normalization ------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+359 88 123 4567", "+359881234567"),
        ("0888-123-456", "0888123456"),
        ("(088) 812 3456", "0888123456"),
    ],
)
def test_normalize_patient_phone_strips_cosmetic_formatting(raw, expected):
    # Cosmetic separators (space/dash/parens) never create a spurious
    # distinct value - only digits and a single leading '+' survive.
    assert normalize_patient_phone(raw) == expected


def test_normalize_patient_phone_rejects_multiple_plus_signs():
    with pytest.raises(ValueError, match="single leading"):
        normalize_patient_phone("+359+888123456")


def test_normalize_patient_phone_rejects_empty_after_stripping():
    with pytest.raises(ValueError, match="at least one digit"):
        normalize_patient_phone("+")


def test_normalize_patient_phone_rejects_non_leading_plus():
    with pytest.raises(ValueError, match="single leading"):
        normalize_patient_phone("359+888")
