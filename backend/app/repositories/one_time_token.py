import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.membership import MembershipRole
from app.models.one_time_token import OneTimeToken, TokenPurpose


class OneTimeTokenRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create_password_reset(
        self, user_id: uuid.UUID, token_hash: str, expires_at: datetime
    ) -> OneTimeToken:
        token = OneTimeToken(
            token_hash=token_hash,
            purpose=TokenPurpose.PASSWORD_RESET,
            user_id=user_id,
            expires_at=expires_at,
        )
        self._db.add(token)
        self._db.flush()
        return token

    def create_invitation(
        self,
        tenant_id: uuid.UUID,
        invited_role: MembershipRole,
        invitee_email: str,
        inviter_user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> OneTimeToken:
        token = OneTimeToken(
            token_hash=token_hash,
            purpose=TokenPurpose.INVITATION_ACCEPT,
            tenant_id=tenant_id,
            invited_role=invited_role,
            invitee_email=invitee_email,
            inviter_user_id=inviter_user_id,
            expires_at=expires_at,
        )
        self._db.add(token)
        self._db.flush()
        return token

    def get_by_token_hash(self, token_hash: str, purpose: TokenPurpose) -> OneTimeToken | None:
        stmt = select(OneTimeToken).where(
            OneTimeToken.token_hash == token_hash, OneTimeToken.purpose == purpose
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def consume(self, token: OneTimeToken, at: datetime) -> None:
        token.consumed_at = at
        self._db.flush()

    def revoke_all_password_reset_for_user(self, user_id: uuid.UUID, at: datetime) -> None:
        stmt = select(OneTimeToken).where(
            OneTimeToken.user_id == user_id,
            OneTimeToken.purpose == TokenPurpose.PASSWORD_RESET,
            OneTimeToken.consumed_at.is_(None),
            OneTimeToken.revoked_at.is_(None),
        )
        for token in self._db.execute(stmt).scalars().all():
            token.revoked_at = at
        self._db.flush()
