"""Audit log table access."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts

ACTOR_SYSTEM = "_system"
ACTOR_ADMIN = "_admin"


@dataclass(frozen=True)
class AuditRow:
    id: int
    ts: int
    actor: str | None
    action: str
    target: str | None
    payload: str | None

    @classmethod
    def from_row(cls, r: DbRow) -> AuditRow:
        return cls(
            id=r["id"],
            ts=r["ts"],
            actor=r["actor"],
            action=r["action"],
            target=r["target"],
            payload=r["payload"],
        )


class AuditRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def write(
        self,
        *,
        actor: str | None,
        action: str,
        target: str | None = None,
        payload: str | None = None,
        ts: int | None = None,
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO audit_log(ts, actor, action, target, payload) VALUES (?, ?, ?, ?, ?)",
                (now_ts() if ts is None else ts, actor, action, target, payload or None),
            )

    def system_event(
        self,
        action: str,
        target: str | None = None,
        payload: str | None = None,
    ) -> None:
        """Write an audit event with actor = ``_system``."""
        self.write(actor=ACTOR_SYSTEM, action=action, target=target, payload=payload)

    def admin_event(
        self,
        action: str,
        target: str | None = None,
        payload: str | None = None,
    ) -> None:
        """Write an audit event with actor = ``_admin``."""
        self.write(actor=ACTOR_ADMIN, action=action, target=target, payload=payload)

    def user_event(
        self,
        username: str,
        action: str,
        target: str | None = None,
        payload: str | None = None,
    ) -> None:
        """Write an audit event with actor = username."""
        self.write(actor=username, action=action, target=target, payload=payload)

    def query(
        self,
        *,
        since: int | None = None,
        actor: str | None = None,
        action: str | None = None,
        target: str | None = None,
        retain_since: int | None = None,
        retained_actions: Collection[str] = (),
        limit: int = 100,
    ) -> list[AuditRow]:
        conditions: list[str] = []
        params: list[object] = []
        if since is not None:
            conditions.append("ts >= ?")
            params.append(since)
        if actor is not None:
            conditions.append("actor = ?")
            params.append(actor)
        if action is not None:
            conditions.append("action = ?")
            params.append(action)
        if target is not None:
            conditions.append("target = ?")
            params.append(target)
        if retain_since is not None and retained_actions:
            placeholders = ",".join("?" for _ in retained_actions)
            conditions.append(f"(ts >= ? OR action NOT IN ({placeholders}))")
            params.extend((retain_since, *retained_actions))
        sql = "SELECT * FROM audit_log"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, AuditRow)

    def write_target_event(
        self,
        *,
        target: str,
        actor: str,
        action: str,
        payload: str,
        since: int,
        should_write: Callable[[list[AuditRow]], bool],
    ) -> None:
        with self._db.transaction() as conn:
            if self._db.dialect == "postgresql":
                # Serialize callbacks against the first event in this trail.
                conn.execute(
                    "SELECT id FROM audit_log WHERE target = ? AND actor = ? AND ts >= ? "
                    "ORDER BY id LIMIT 1 FOR UPDATE",
                    (target, actor, since),
                ).fetchone()
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE target = ? AND actor = ? AND ts >= ? ORDER BY id",
                (target, actor, since),
            ).fetchall()
            if should_write(map_rows(rows, AuditRow)):
                conn.execute(
                    "INSERT INTO audit_log(ts, actor, action, target, payload) VALUES (?, ?, ?, ?, ?)",
                    (now_ts(), actor, action, target, payload),
                )

    def delete_before(self, cutoff_ts: int, *, actions: Collection[str]) -> int:
        """Delete expired rows only for the specified actions."""
        if not actions:
            return 0
        placeholders = ",".join("?" for _ in actions)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                f"DELETE FROM audit_log WHERE ts < ? AND action IN ({placeholders})",
                (cutoff_ts, *actions),
            )
        return int(cursor.rowcount or 0)
