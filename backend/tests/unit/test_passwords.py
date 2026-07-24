import pytest

from app.core.passwords import (
    InvalidPasswordError,
    hash_password,
    needs_rehash,
    validate_password_policy,
    verify_password,
)


def test_policy_accepts_a_long_passphrase():
    validate_password_policy("correct horse battery staple")


def test_policy_rejects_too_short():
    with pytest.raises(InvalidPasswordError):
        validate_password_policy("short")
    with pytest.raises(InvalidPasswordError):
        validate_password_policy("a" * 11)


def test_policy_rejects_empty():
    with pytest.raises(InvalidPasswordError):
        validate_password_policy("")


def test_policy_rejects_whitespace_only():
    with pytest.raises(InvalidPasswordError):
        validate_password_policy("            ")


def test_policy_rejects_too_long():
    with pytest.raises(InvalidPasswordError):
        validate_password_policy("a" * 257)


def test_policy_accepts_at_minimum_length():
    validate_password_policy("a" * 12)


def test_policy_does_not_require_mixed_character_classes():
    validate_password_policy("aaaaaaaaaaaaaaaaaaaa")


def test_hash_password_rejects_policy_violation():
    with pytest.raises(InvalidPasswordError):
        hash_password("too-short")


def test_hash_and_verify_roundtrip():
    password = "a reasonably long passphrase 123"
    hashed = hash_password(password)
    assert verify_password(password, hashed)


def test_verify_rejects_wrong_password():
    hashed = hash_password("a reasonably long passphrase 123")
    assert not verify_password("a different passphrase entirely", hashed)


def test_verify_rejects_malformed_hash_without_raising():
    assert not verify_password("whatever password", "not-a-real-argon2-hash")


def test_hash_never_equals_plaintext():
    password = "a reasonably long passphrase 123"
    assert hash_password(password) != password


def test_needs_rehash_is_false_for_a_freshly_hashed_password():
    assert not needs_rehash(hash_password("a reasonably long passphrase 123"))
