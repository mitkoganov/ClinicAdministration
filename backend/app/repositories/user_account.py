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
        user.password_hash = password_hash
        user.password_changed_at = changed_at
        self._db.flush()

    def record_successful_login(self, user: UserAccount, at: datetime) -> None:
        user.last_successful_login_at = at
        self._db.flush()
