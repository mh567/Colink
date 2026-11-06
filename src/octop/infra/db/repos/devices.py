"""Persistent native device identities and administrator trust decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, now_ts
from octop.infra.utils.ulid import new_ulid

DeviceStatus = Literal["pending", "approved", "revoked"]


@dataclass(frozen=True)
class DeviceRow:
    device_id: str
    owner_user_id: int
    owner_username: str
    public_key: str
    name: str
    platform: str
    status: DeviceStatus
    created_at: int
    last_seen: int | None

    @classmethod
    def from_row(cls, row: DbRow) -> DeviceRow:
        return cls(**{field: row[field] for field in cls.__dataclass_fields__})


_SELECT = "SELECT d.*, u.username AS owner_username FROM employee_devices d JOIN users u ON u.id = d.owner_user_id"


class DeviceRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def get(self, device_id: str) -> DeviceRow | None:
        with self._db.connect() as conn:
            row = conn.execute(_SELECT + " WHERE d.device_id = ?", (device_id,)).fetchone()
        return DeviceRow.from_row(row) if row is not None else None

    def get_by_key(self, public_key: str) -> DeviceRow | None:
        with self._db.connect() as conn:
            row = conn.execute(_SELECT + " WHERE d.public_key = ?", (public_key,)).fetchone()
        return DeviceRow.from_row(row) if row is not None else None

    def list(self, owner_user_id: int | None = None) -> list[DeviceRow]:
        sql = _SELECT
        args: tuple[int, ...] = ()
        if owner_user_id is not None:
            sql += " WHERE d.owner_user_id = ?"
            args = (owner_user_id,)
        with self._db.connect() as conn:
            rows = conn.execute(sql + " ORDER BY d.created_at, d.device_id", args).fetchall()
        return [DeviceRow.from_row(row) for row in rows]

    def enroll(self, *, user_id: int, public_key: str, name: str, platform: str) -> DeviceRow:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO employee_devices(device_id, owner_user_id, public_key, name, platform, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(public_key) DO NOTHING",
                (new_ulid(), user_id, public_key, name, platform, now_ts()),
            )
        row = self.get_by_key(public_key)
        assert row is not None
        return row

    def set_status(self, device_id: str, status: DeviceStatus) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE employee_devices SET status = ? WHERE device_id = ?", (status, device_id)
            )

    def touch(self, device_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE employee_devices SET last_seen = ? WHERE device_id = ?",
                (now_ts(), device_id),
            )
