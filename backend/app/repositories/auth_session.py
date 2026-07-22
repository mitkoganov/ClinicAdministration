import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth_session import AuthSession


class AuthSessionRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(
        self,
        user_id: uuid.UUID,
        session_token_hash: str,
        csrf_token_hash: str,
        absolute_expires_at: datetime,
        idle_expires_at: datetime,
    ) -> AuthSession:
        session = AuthSession(
            user_id=user_id,
            session_token_hash=session_token_hash,
            csrf_token_hash=csrf_token_hash,
            absolute_expires_at=absolute_expires_at,
            idle_expires_at=idle_expires_at,
        )
        self._db.add(session)
        self._db.flush()
        return session

    def get_by_token_hash(self, session_token_hash: str) -> AuthSession | None:
        stmt = select(AuthSession).where(AuthSession.session_token_hash == session_token_hash)
        return self._db.execute(stmt).scalar_one_or_none()

    def touch(self, session: AuthSession, now: datetime, idle_expires_at: datetime) -> None:
        session.last_seen_at = now
        session.idle_expires_at = idle_expires_at
        self._db.flush()

    def select_tenant(self, session: AuthSession, tenant_id: uuid.UUID | None) -> None:
        session.selected_tenant_id = tenant_id
        self._db.flush()

    def revoke(self, session: AuthSession, reason: str, at: datetime) -> None:
        if session.revoked_at is not None:
            return
        session.revoked_at = at
        session.revocation_reason = reason
        self._db.flush()

    def revoke_all_for_user(
        self,
        user_id: uuid.UUID,
        reason: str,
        at: datetime,
        *,
        except_session_id: uuid.UUID | None = None,
    ) -> int:
        stmt = select(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
        )
        sessions = self._db.execute(stmt).scalars().all()
        revoked_count = 0
        for session in sessions:
            if except_session_id is not None and session.id == except_session_id:
                continue
            session.revoked_at = at
            session.revocation_reason = reason
            revoked_count += 1
        self._db.flush()
        return revoked_count
