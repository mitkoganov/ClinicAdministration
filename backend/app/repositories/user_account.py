import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user_account import UserAccount


class UserAccountRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_normalized_email(self, normalized_email: str) -> UserAccount | None:
        stmt = select(UserAccount).where(UserAccount.normalized_email == normalized_email)
        return self._db.execute(stmt).scalar_one_or_none()

    def get_by_id(self, user_id: uuid.UUID) -> UserAccount | None:
        return self._db.get(UserAccount, user_id)

    def create(self, normalized_email: str, display_name: str, password_hash: str) -> UserAccount:
        account = UserAccount(
            normalized_email=normalized_email,
            display_name=display_name,
            password_hash=password_hash,
        )
        self._db.add(account)
        self._db.flush()
        return account

    def update_password_hash(
        self, user: UserAccount, password_hash: str, changed_at: datetime
    ) -> None:
        """For a REAL credential change only - authenticated change-
        password or password-reset confirmation. Advances
        `password_changed_at`, which callers may use for password-age
        policy or audit interpretation. Never call this for a transparent
        rehash (see `rehash_password_hash`) - that is not a credential
        change and must not reset password-age tracking."""
        user.password_hash = password_hash
        user.password_changed_at = changed_at
        self._db.flush()

    def rehash_password_hash(self, user: UserAccount, password_hash: str) -> None:
        """Replaces only the stored hash bytes - used when `needs_rehash`
        determines the existing hash was produced with outdated Argon2
        parameters, discovered incidentally during an otherwise-successful
        login. The user did not change their password, so
        `password_changed_at` must stay exactly as it was; this is
        maintenance of the hash's cost parameters, not a credential
        change."""
        user.password_hash = password_hash
        self._db.flush()

    def record_successful_login(self, user: UserAccount, at: datetime) -> None:
        user.last_successful_login_at = at
        self._db.flush()
