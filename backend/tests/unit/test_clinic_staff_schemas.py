import uuid

import pytest
from pydantic import ValidationError

from app.models.membership import MembershipRole, MembershipStatus
from app.schemas.clinic import ClinicUpdate
from app.schemas.staff import StaffMemberCreate, StaffMemberUpdate


def test_clinic_update_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ClinicUpdate.model_validate({"name": "Acme Clinic", "status": "inactive"})


def test_clinic_update_rejects_tenant_id_override():
    with pytest.raises(ValidationError):
        ClinicUpdate.model_validate({"name": "Acme Clinic", "tenant_id": str(uuid.uuid4())})


def test_clinic_update_accepts_name_only():
    payload = ClinicUpdate.model_validate({"name": "Acme Clinic"})
    assert payload.name == "Acme Clinic"


def test_clinic_update_rejects_empty_name():
    with pytest.raises(ValidationError):
        ClinicUpdate.model_validate({"name": ""})


def test_staff_member_create_rejects_tenant_id_override():
    with pytest.raises(ValidationError):
        StaffMemberCreate.model_validate(
            {"user_id": str(uuid.uuid4()), "role": "operator", "tenant_id": str(uuid.uuid4())}
        )


def test_staff_member_create_rejects_malformed_role():
    with pytest.raises(ValidationError):
        StaffMemberCreate.model_validate({"user_id": str(uuid.uuid4()), "role": "superadmin"})


def test_staff_member_update_requires_at_least_one_field():
    with pytest.raises(ValidationError):
        StaffMemberUpdate.model_validate({})


def test_staff_member_update_accepts_role_only():
    payload = StaffMemberUpdate.model_validate({"role": "manager"})
    assert payload.role == MembershipRole.MANAGER
    assert payload.status is None


def test_staff_member_update_accepts_status_only():
    payload = StaffMemberUpdate.model_validate({"status": "inactive"})
    assert payload.status == MembershipStatus.INACTIVE
    assert payload.role is None


def test_staff_member_update_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        StaffMemberUpdate.model_validate({"role": "manager", "user_id": str(uuid.uuid4())})
