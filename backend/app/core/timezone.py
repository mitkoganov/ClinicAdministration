"""IANA timezone validation - the single place a tenant/clinic timezone
name is validated, so every model/service/schema shares one definition of
"valid" instead of re-implementing this check.

Never hardcode a specific timezone (e.g. "Europe/Sofia") in availability or
scheduling logic - always read it from `Tenant.timezone` and pass it through
`resolve_timezone` here."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TENANT_TIMEZONE = "Europe/Sofia"


class InvalidTimezoneError(ValueError):
    """`value` is not a resolvable IANA timezone name."""


def validate_timezone_name(value: str) -> str:
    """Raises `InvalidTimezoneError` unless `value` is a real, resolvable
    IANA timezone name (e.g. "Europe/Sofia", "UTC"). Returns the value
    unchanged (no normalization) on success - IANA names are already
    case-sensitive canonical identifiers, unlike the slug/email
    normalization elsewhere in this codebase."""
    if not value or not value.strip():
        raise InvalidTimezoneError("Timezone must not be empty.")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTimezoneError(f"'{value}' is not a recognized IANA timezone name.") from exc
    return value


def resolve_timezone(name: str) -> ZoneInfo:
    """Assumes `name` was already validated (e.g. at write time via
    `validate_timezone_name`) - raises the same `InvalidTimezoneError` if
    not, rather than a raw `ZoneInfoNotFoundError`, so every caller catches
    one exception type."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTimezoneError(f"'{name}' is not a recognized IANA timezone name.") from exc
