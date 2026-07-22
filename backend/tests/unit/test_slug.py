import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from app.core.slug import (
    MAX_SLUG_LENGTH,
    MIN_SLUG_LENGTH,
    InvalidSlugError,
    normalize_and_validate_slug,
    normalize_slug,
    validate_slug,
)
from app.models.tenant import Tenant, TenantStatus


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme Clinic", "acme-clinic"),
        ("  Acme   Clinic  ", "acme-clinic"),
        ("ACME_CLINIC!!", "acme-clinic"),
        ("acme-clinic", "acme-clinic"),
    ],
)
def test_normalize_slug_is_deterministic(raw, expected):
    assert normalize_slug(raw) == expected


def test_validate_slug_accepts_a_valid_normalized_slug():
    validate_slug("acme-clinic")


@pytest.mark.parametrize("raw", ["", "   ", "!!!", "___", "---"])
def test_whitespace_or_punctuation_only_input_normalizes_to_empty_and_is_rejected(raw):
    normalized = normalize_slug(raw)
    assert normalized == ""
    with pytest.raises(InvalidSlugError):
        validate_slug(normalized)


def test_normalize_and_validate_slug_rejects_punctuation_only_input():
    with pytest.raises(InvalidSlugError):
        normalize_and_validate_slug("### ???")


def test_normalize_and_validate_slug_accepts_valid_input():
    assert normalize_and_validate_slug("Acme Clinic") == "acme-clinic"


def test_single_character_slug_is_rejected_below_minimum_length():
    assert MIN_SLUG_LENGTH == 2
    with pytest.raises(InvalidSlugError):
        validate_slug("a")


def test_slug_at_max_length_is_accepted():
    validate_slug("a" * MAX_SLUG_LENGTH)


def test_slug_over_max_length_is_rejected():
    with pytest.raises(InvalidSlugError):
        validate_slug("a" * (MAX_SLUG_LENGTH + 1))


def test_tenant_model_rejects_invalid_slug_on_construction():
    with pytest.raises(InvalidSlugError):
        Tenant(name="Bad", slug="", status=TenantStatus.ACTIVE)


def test_tenant_model_rejects_invalid_slug_on_assignment():
    tenant = Tenant(name="Ok", slug="ok-slug", status=TenantStatus.ACTIVE)
    with pytest.raises(InvalidSlugError):
        tenant.slug = "!!"


@pytest.mark.integration
def test_database_check_constraint_rejects_empty_slug_bypassing_the_orm(db_session):
    # Bypasses Tenant's @validates hook entirely via a raw SQL insert,
    # proving the database CHECK constraint is a real, independent second
    # layer of enforcement - not just relying on the ORM to be well-behaved.
    with pytest.raises((IntegrityError, DataError)):
        db_session.execute(
            text(
                "INSERT INTO tenants (id, name, slug, status, created_at, updated_at) "
                "VALUES (:id, 'Bad', '', 'active', now(), now())"
            ),
            {"id": str(uuid.uuid4())},
        )
