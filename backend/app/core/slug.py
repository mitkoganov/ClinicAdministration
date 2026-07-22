"""Tenant slug normalization and validation.

A small leaf module with no dependencies on models or services, so both can
import it safely: `app.models.tenant` needs `validate_slug` for a SQLAlchemy
`@validates` hook, and `app.services.tenant_service` re-exports
`normalize_slug` for its existing callers. Importing either models or
services from here would create a cycle; this module must stay a leaf.
"""

import re

_SLUG_COLLAPSE_RE = re.compile(r"[^a-z0-9]+")
_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

MIN_SLUG_LENGTH = 2
MAX_SLUG_LENGTH = 200


class InvalidSlugError(ValueError):
    """Raised when a tenant slug fails to normalize to an acceptable value."""


def normalize_slug(raw: str) -> str:
    """Deterministic slug normalization: lowercase, and any run of
    non-alphanumeric characters collapses to a single hyphen, with leading/
    trailing hyphens stripped. Two inputs that normalize identically must be
    treated as the same slug (enforced by the unique constraint on the
    already-normalized column). Does not validate the result - see
    `validate_slug`."""
    collapsed = _SLUG_COLLAPSE_RE.sub("-", raw.strip().lower())
    return collapsed.strip("-")


def validate_slug(normalized: str) -> None:
    """Raises `InvalidSlugError` if `normalized` is not an acceptable,
    already-normalized tenant slug. Callers must normalize first - this
    function does not normalize, only validates."""
    if not normalized:
        raise InvalidSlugError("Slug must not be empty after normalization.")
    if len(normalized) < MIN_SLUG_LENGTH:
        raise InvalidSlugError(
            f"Slug must be at least {MIN_SLUG_LENGTH} characters after "
            f"normalization, got {normalized!r}."
        )
    if len(normalized) > MAX_SLUG_LENGTH:
        raise InvalidSlugError(
            f"Slug must be at most {MAX_SLUG_LENGTH} characters after "
            f"normalization, got {len(normalized)} characters."
        )
    if not _SLUG_PATTERN.fullmatch(normalized):
        raise InvalidSlugError(
            "Slug must contain only lowercase letters, digits, and single "
            f"hyphens between segments, got {normalized!r}."
        )


def normalize_and_validate_slug(raw: str) -> str:
    """Normalize then validate in one call - the function any future
    tenant-creation code should use end to end."""
    normalized = normalize_slug(raw)
    validate_slug(normalized)
    return normalized
