import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.clinic_room import ClinicRoom, ClinicRoomStatus


class ClinicRoomRepository:
    """Every method takes `tenant_id` explicitly and scopes the query in
    one shot - a foreign-tenant row and a missing row always produce the
    identical "no row" result, matching MembershipRepository's precedent."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, tenant_id: uuid.UUID, room_id: uuid.UUID) -> ClinicRoom | None:
        stmt = select(ClinicRoom).where(ClinicRoom.tenant_id == tenant_id, ClinicRoom.id == room_id)
        return self._db.execute(stmt).scalar_one_or_none()

    def get_by_code(self, tenant_id: uuid.UUID, code: str) -> ClinicRoom | None:
        stmt = select(ClinicRoom).where(ClinicRoom.tenant_id == tenant_id, ClinicRoom.code == code)
        return self._db.execute(stmt).scalar_one_or_none()

    def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        status: ClinicRoomStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ClinicRoom], int]:
        conditions = [ClinicRoom.tenant_id == tenant_id]
        if status is not None:
            conditions.append(ClinicRoom.status == status)

        total = self._db.execute(
            select(func.count()).select_from(ClinicRoom).where(*conditions)
        ).scalar_one()
        stmt = (
            select(ClinicRoom)
            .where(*conditions)
            .order_by(ClinicRoom.name.asc(), ClinicRoom.id.asc())
            .limit(limit)
            .offset(offset)
        )
        items = list(self._db.execute(stmt).scalars().all())
        return items, total

    def create(
        self, tenant_id: uuid.UUID, *, name: str, code: str, description: str | None
    ) -> ClinicRoom:
        room = ClinicRoom(tenant_id=tenant_id, name=name, code=code, description=description)
        self._db.add(room)
        self._db.flush()
        return room

    def update(
        self,
        tenant_id: uuid.UUID,
        room_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        status: ClinicRoomStatus | None = None,
    ) -> ClinicRoom | None:
        room = self.get_by_id(tenant_id, room_id)
        if room is None:
            return None
        if name is not None:
            room.name = name
        if description is not None:
            room.description = description
        if status is not None:
            room.status = status
        self._db.flush()
        return room
