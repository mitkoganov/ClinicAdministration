"""Password hashing and policy - the single place either is defined.

Hashing uses Argon2id via `argon2-cffi` (an audited, purpose-built library -
never a hand-rolled hash). `PasswordHasher`'s defaults already select
Argon2id with library-managed unique salts and reasonable cost parameters;
this module only wraps it so callers never touch the library directly and
never need to re-derive the policy (minimum length, max length, rehash
detection) themselves.
"""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# Documented password policy (task.md "Password security"): a minimum
# length only - no mandatory character-class mix, since that historically
# pushes users toward weaker, harder-to-remember passwords instead of
# long passphrases or password-manager-generated ones. The maximum guards
# against a caller submitting a multi-megabyte "password" to force
# expensive hashing work (a hashing-cost denial-of-service vector), not
# against legitimate long passphrases.
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256

_hasher = PasswordHasher()


class InvalidPasswordError(ValueError):
    """A password fails the documented policy. Carries a safe, user-facing
    message only - never echoes the rejected password back."""


def validate_password_policy(password: str) -> None:
    """Raises `InvalidPasswordError` if `password` fails the documented
    policy. Never truncates - a too-long input is rejected outright, not
    silently cut down to size (silent truncation would let two different
    passwords beyond the limit hash identically)."""
    if not password or not password.strip():
        raise InvalidPasswordError("Password must not be empty or whitespace-only.")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise InvalidPasswordError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise InvalidPasswordError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters.")


def hash_password(password: str) -> str:
    """Validates the password against policy first, then hashes it.
    Never logs the password - the caller must not log it either."""
    validate_password_policy(password)
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-effort verification via argon2-cffi - never a manual
    string comparison of hashes. Returns False for any malformed hash or
    mismatch; never raises, so callers can treat this as a plain boolean
    check without needing to know argon2's exception types."""
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        # A malformed/unrecognized hash (e.g. from a future algorithm
        # change) must fail closed as "does not match", never raise an
        # unhandled 500 for what is, from the caller's perspective, just a
        # failed login attempt.
        return False


def needs_rehash(password_hash: str) -> bool:
    """True if `password_hash` was produced with parameters older than
    this module's current defaults - callers should re-hash and persist
    the new hash immediately after a successful verification."""
    return _hasher.check_needs_rehash(password_hash)
