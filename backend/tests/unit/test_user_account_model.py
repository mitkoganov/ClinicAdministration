from app.models.user_account import UserAccount, normalize_email


def test_normalize_email_lowercases_and_trims():
    assert normalize_email("  Alice@Example.COM  ") == "alice@example.com"


def test_normalize_email_is_idempotent():
    once = normalize_email("Alice@Example.com")
    assert normalize_email(once) == once


def test_model_normalizes_email_on_construction():
    user = UserAccount(
        normalized_email="  Alice@Example.COM  ",
        display_name="Alice",
        password_hash="irrelevant-for-this-test",
    )
    assert user.normalized_email == "alice@example.com"


def test_model_repr_excludes_password_hash():
    user = UserAccount(
        normalized_email="alice@example.com",
        display_name="Alice",
        password_hash="super-secret-hash-value",
    )
    assert "super-secret-hash-value" not in repr(user)
