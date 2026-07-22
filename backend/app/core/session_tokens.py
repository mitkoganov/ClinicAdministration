"""Opaque high-entropy tokens for sessions, CSRF, and one-time (password
reset / invitation) purposes - and their storage hash.

These are NOT passwords: the raw value is already high-entropy random
(256 bits from `secrets.token_urlsafe`), so hashing it for storage only
needs a fast, deterministic cryptographic hash (SHA-256) to defend the
database against being a lookup oracle if it ever leaks - never the slow,
salted `app.core.passwords` hasher, which exists to defend low-entropy
human-chosen passwords against offline brute force. Using the password
hasher here would be both wrong (different threat model) and needlessly
slow on every single request.
"""

import hashlib
import hmac
import secrets

_TOKEN_BYTES = 32  # 256 bits of entropy.


def generate_token() -> str:
    """A new, unpredictable, URL-safe random token - used for session
    tokens, CSRF tokens, password-reset tokens, and invitation tokens
    alike. Never logged by any caller."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    """Deterministic SHA-256 hex digest - what's actually persisted.
    Deterministic (not salted) is required here, unlike passwords: the
    only way to look up a session/token row is by re-hashing the
    caller-supplied raw value and querying for an exact match, which is
    exactly what `app.repositories.auth_session.get_by_token_hash` and the
    one-time-token equivalent do."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def tokens_match(raw_token: str, expected_hash: str) -> bool:
    """Constant-time comparison of a freshly-hashed raw token against a
    stored hash - avoids a timing side-channel on the comparison itself
    (the hash lookup that finds `expected_hash` in the first place is a
    separate, unavoidable database-timing question, not addressed here)."""
    return hmac.compare_digest(hash_token(raw_token), expected_hash)
