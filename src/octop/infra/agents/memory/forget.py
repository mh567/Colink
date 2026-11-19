"""Actual forgetting of personal memory atoms across SQLite and PostgreSQL (T17).

Physical deletion of memories, associated candidates, raw events, memory tree
nodes, and internal journal entries. Soft-deactivation or deprecated flags are
NOT used for actual forgetting.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from octop.api.common.memory_client import invalidate_cached_memory
from octop.infra.agents.memory.backend import open_memory_kwargs
from octop.infra.agents.workspace.dir import host_system_dir
from octop.infra.errors import ErrorCode, OctopError

logger = logging.getLogger(__name__)

_TOMBSTONES_FILENAME = "forgotten_memories.json"


def _tombstones_file(workspace_dir: Path, cfg: dict[str, Any] | None = None) -> Path:
    system_dir = host_system_dir(workspace_dir, cfg)
    return system_dir / _TOMBSTONES_FILENAME


def get_forgotten_memories(workspace_dir: Path, namespace: str) -> list[dict[str, Any]]:
    """Read persisted forgotten memory tombstones for a workspace/namespace."""
    path = _tombstones_file(workspace_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [
                item
                for item in data
                if isinstance(item, dict) and item.get("namespace") == namespace
            ]
    except Exception:
        logger.warning("failed to read forgotten memories tombstone at %s", path, exc_info=True)
    return []


def _record_tombstone(
    workspace_dir: Path,
    namespace: str,
    atom_id: str,
    assertion: str,
    entity_name: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> None:
    path = _tombstones_file(workspace_dir, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except Exception:
            existing = []
    # Deduplicate by (namespace, atom_id)
    filtered = [
        x for x in existing if not (x.get("namespace") == namespace and x.get("atom_id") == atom_id)
    ]
    filtered.append(
        {
            "namespace": namespace,
            "atom_id": atom_id,
            "assertion": assertion,
            "entity_name": entity_name,
            "forgotten_at": datetime.now(UTC).isoformat(),
        }
    )
    path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")


def _record_postgres_tombstone(
    conn: Any, namespace: str, atom_id: str, assertion: str, entity_name: str | None
) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS octop_memory.forgotten_memories (
            namespace TEXT NOT NULL,
            atom_id TEXT NOT NULL,
            assertion TEXT NOT NULL,
            entity_name TEXT,
            forgotten_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (namespace, atom_id)
        )
    """)
    cur.execute(
        """
        INSERT INTO octop_memory.forgotten_memories (namespace, atom_id, assertion, entity_name, forgotten_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (namespace, atom_id) DO UPDATE SET
            assertion = EXCLUDED.assertion,
            entity_name = EXCLUDED.entity_name,
            forgotten_at = EXCLUDED.forgotten_at
    """,
        (namespace, atom_id, assertion, entity_name),
    )


def _extract_raw_event_ids(raw_val: Any) -> list[str]:
    if isinstance(raw_val, list):
        return [str(x) for x in raw_val if x]
    if isinstance(raw_val, str):
        try:
            parsed = json.loads(raw_val)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x]
        except Exception:
            pass
    return []


def _forget_atom_sqlite(
    db_path: Path,
    namespace: str,
    atom_id: str,
) -> dict[str, Any]:
    from octop_memory.storage.backends.sqlite import register_fts_functions

    prefix = f"{namespace}_"
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        register_fts_functions(conn)
        conn.execute("PRAGMA foreign_keys = OFF")
        cur = conn.cursor()
        # Find atom
        row = cur.execute(
            f"SELECT entity_id, candidate_id, raw_event_ids, quote_event_id, assertion FROM {prefix}atoms WHERE id = ?",
            (atom_id,),
        ).fetchone()
        if not row:
            return {"forgotten": False, "found": False, "atom_id": atom_id}

        entity_id, candidate_id, raw_event_ids_raw, quote_event_id, assertion = row
        raw_event_ids = set(_extract_raw_event_ids(raw_event_ids_raw))
        if quote_event_id:
            raw_event_ids.add(str(quote_event_id))

        entity_name: str | None = None
        if entity_id:
            e_row = cur.execute(
                f"SELECT canonical_name FROM {prefix}entities WHERE id = ?", (entity_id,)
            ).fetchone()
            if e_row:
                entity_name = str(e_row[0])

        if candidate_id:
            c_row = cur.execute(
                f"SELECT raw_event_ids FROM {prefix}candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
            if c_row:
                raw_event_ids.update(_extract_raw_event_ids(c_row[0]))

        # Delete from memory_nodes (atom leaf)
        cur.execute(f"DELETE FROM {prefix}memory_nodes WHERE atom_id = ?", (atom_id,))
        deleted_nodes = cur.rowcount

        # Delete from atoms
        cur.execute(f"DELETE FROM {prefix}atoms WHERE id = ?", (atom_id,))
        deleted_atoms = cur.rowcount

        # Delete from candidates
        deleted_candidates = 0
        if candidate_id:
            cur.execute(f"DELETE FROM {prefix}candidates WHERE id = ?", (candidate_id,))
            deleted_candidates = cur.rowcount

        # Delete from raw_events
        deleted_raw_events = 0
        if raw_event_ids:
            placeholders = ",".join("?" for _ in raw_event_ids)
            cur.execute(
                f"DELETE FROM {prefix}raw_events WHERE id IN ({placeholders})", list(raw_event_ids)
            )
            deleted_raw_events = cur.rowcount

        # Delete from journal
        cur.execute(
            f"DELETE FROM {prefix}journal WHERE target_atom_id = ? OR (target_candidate_id = ? AND ? != '')",
            (atom_id, candidate_id or "", candidate_id or ""),
        )
        deleted_journal = cur.rowcount

        # Update or clean entity
        if entity_id:
            cur.execute(
                f"UPDATE {prefix}entities SET atom_count = MAX(0, atom_count - 1) WHERE id = ?",
                (entity_id,),
            )
            cnt = cur.execute(
                f"SELECT atom_count FROM {prefix}entities WHERE id = ?", (entity_id,)
            ).fetchone()
            if cnt and cnt[0] == 0:
                cur.execute(f"DELETE FROM {prefix}entity_pages WHERE entity_id = ?", (entity_id,))
                cur.execute(f"DELETE FROM {prefix}aliases WHERE entity_id = ?", (entity_id,))
                cur.execute(
                    f"DELETE FROM {prefix}thread_active_entities WHERE entity_id = ?", (entity_id,)
                )
                cur.execute(f"DELETE FROM {prefix}memory_nodes WHERE id = ?", (entity_id,))
                cur.execute(f"DELETE FROM {prefix}entities WHERE id = ?", (entity_id,))

        conn.commit()

    return {
        "forgotten": True,
        "found": True,
        "atom_id": atom_id,
        "assertion": assertion,
        "entity_name": entity_name,
        "deleted_atoms": deleted_atoms,
        "deleted_candidates": deleted_candidates,
        "deleted_raw_events": deleted_raw_events,
        "deleted_nodes": deleted_nodes,
        "deleted_journal": deleted_journal,
        "backend": "sqlite",
    }


def _forget_atom_postgres(
    dsn: str,
    namespace: str,
    atom_id: str,
) -> dict[str, Any]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, autocommit=False, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT entity_id, candidate_id, raw_event_ids, quote_event_id, assertion "
                "FROM octop_memory.atoms WHERE namespace = %s AND id = %s FOR UPDATE",
                (namespace, atom_id),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return {"forgotten": False, "found": False, "atom_id": atom_id}

            entity_id = row["entity_id"]
            candidate_id = row["candidate_id"]
            assertion = row["assertion"]
            raw_event_ids = set(_extract_raw_event_ids(row["raw_event_ids"]))
            if row.get("quote_event_id"):
                raw_event_ids.add(str(row["quote_event_id"]))

            entity_name: str | None = None
            if entity_id:
                cur.execute(
                    "SELECT canonical_name FROM octop_memory.entities WHERE namespace = %s AND id = %s",
                    (namespace, entity_id),
                )
                e_row = cur.fetchone()
                if e_row:
                    entity_name = str(e_row["canonical_name"])

            if candidate_id:
                cur.execute(
                    "SELECT raw_event_ids FROM octop_memory.candidates WHERE namespace = %s AND id = %s",
                    (namespace, candidate_id),
                )
                c_row = cur.fetchone()
                if c_row:
                    raw_event_ids.update(_extract_raw_event_ids(c_row["raw_event_ids"]))

            # Delete from memory_nodes
            cur.execute(
                "DELETE FROM octop_memory.memory_nodes WHERE namespace = %s AND atom_id = %s",
                (namespace, atom_id),
            )
            deleted_nodes = cur.rowcount

            # Delete from atoms
            cur.execute(
                "DELETE FROM octop_memory.atoms WHERE namespace = %s AND id = %s",
                (namespace, atom_id),
            )
            deleted_atoms = cur.rowcount

            # Delete from candidates
            deleted_candidates = 0
            if candidate_id:
                cur.execute(
                    "DELETE FROM octop_memory.candidates WHERE namespace = %s AND id = %s",
                    (namespace, candidate_id),
                )
                deleted_candidates = cur.rowcount

            # Delete from raw_events
            deleted_raw_events = 0
            if raw_event_ids:
                cur.execute(
                    "DELETE FROM octop_memory.raw_events WHERE namespace = %s AND id = ANY(%s)",
                    (namespace, list(raw_event_ids)),
                )
                deleted_raw_events = cur.rowcount

            # Delete from journal
            cur.execute(
                "DELETE FROM octop_memory.journal WHERE namespace = %s AND (target_atom_id = %s OR target_candidate_id = %s)",
                (namespace, atom_id, candidate_id or ""),
            )
            deleted_journal = cur.rowcount

            # Update or clean entity
            if entity_id:
                cur.execute(
                    "UPDATE octop_memory.entities SET atom_count = GREATEST(0, atom_count - 1) "
                    "WHERE namespace = %s AND id = %s RETURNING atom_count",
                    (namespace, entity_id),
                )
                cnt_row = cur.fetchone()
                if cnt_row and cnt_row["atom_count"] == 0:
                    cur.execute(
                        "DELETE FROM octop_memory.entity_pages WHERE namespace = %s AND entity_id = %s",
                        (namespace, entity_id),
                    )
                    cur.execute(
                        "DELETE FROM octop_memory.aliases WHERE namespace = %s AND entity_id = %s",
                        (namespace, entity_id),
                    )
                    cur.execute(
                        "DELETE FROM octop_memory.thread_active_entities WHERE namespace = %s AND entity_id = %s",
                        (namespace, entity_id),
                    )
                    cur.execute(
                        "DELETE FROM octop_memory.memory_nodes WHERE namespace = %s AND id = %s",
                        (namespace, entity_id),
                    )
                    cur.execute(
                        "DELETE FROM octop_memory.entities WHERE namespace = %s AND id = %s",
                        (namespace, entity_id),
                    )

            # Record PostgreSQL tombstone
            _record_postgres_tombstone(conn, namespace, atom_id, assertion, entity_name)

        conn.commit()

    return {
        "forgotten": True,
        "found": True,
        "atom_id": atom_id,
        "assertion": assertion,
        "entity_name": entity_name,
        "deleted_atoms": deleted_atoms,
        "deleted_candidates": deleted_candidates,
        "deleted_raw_events": deleted_raw_events,
        "deleted_nodes": deleted_nodes,
        "deleted_journal": deleted_journal,
        "backend": "postgres",
    }


def forget_atom_in_store(
    *,
    workspace_dir: Path,
    namespace: str,
    atom_id: str,
    backend: str,
    backend_config: dict[str, Any] | None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute physical forgetting on SQLite or PostgreSQL."""
    if backend == "postgres":
        dsn = (backend_config or {}).get("dsn")
        if not dsn:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "postgres dsn missing for memory backend")
        result = _forget_atom_postgres(dsn, namespace, atom_id)
    else:
        db_path_val = (backend_config or {}).get("db_path")
        db_path = (
            Path(db_path_val)
            if db_path_val
            else host_system_dir(workspace_dir, cfg) / "memory.sqlite"
        )
        if not db_path.is_file():
            return {"forgotten": False, "found": False, "atom_id": atom_id}
        result = _forget_atom_sqlite(db_path, namespace, atom_id)

    if result.get("found"):
        _record_tombstone(
            workspace_dir=workspace_dir,
            namespace=namespace,
            atom_id=atom_id,
            assertion=result.get("assertion", ""),
            entity_name=result.get("entity_name"),
            cfg=cfg,
        )

    return result


def prune_restored_forgotten_memories(
    *,
    workspace_dir: Path,
    namespace: str,
    backend: str = "sqlite",
    backend_config: dict[str, Any] | None = None,
    cfg: dict[str, Any] | None = None,
) -> int:
    """Prune any atoms in the active store that match previously forgotten tombstones."""
    tombstones = get_forgotten_memories(workspace_dir, namespace)
    if not tombstones:
        return 0

    pruned = 0
    for tombstone in tombstones:
        atom_id = tombstone.get("atom_id")
        if not atom_id:
            continue
        res = forget_atom_in_store(
            workspace_dir=workspace_dir,
            namespace=namespace,
            atom_id=atom_id,
            backend=backend,
            backend_config=backend_config,
            cfg=cfg,
        )
        if res.get("found"):
            pruned += 1

    return pruned


async def forget_memory_atom_for_agent(
    server: Any,
    agent_id: str,
    atom_id: str,
    *,
    user: Any,
) -> dict[str, Any]:
    """High-level service entry for forgetting a personal memory atom.

    Enforces that only the owning user may forget memories.
    """
    from octop.api.common.agent import require_agent_owner_row
    from octop.api.common.agent_workspace import resolve_agent_workspace_dir

    require_agent_owner_row(agent_id, user=user, as_user=None, server=server)
    runtime = getattr(server, "app_runtime", None)
    coordinator = runtime.agent_registry.memory_slim if runtime is not None else None
    if coordinator is not None:
        status = coordinator.status(agent_id)
        if isinstance(status, dict) and status.get("phase") in {
            "backing_up",
            "inspecting",
            "cleaning",
            "deduplicating",
            "compacting",
            "draining",
            "validating",
            "backing_up_current",
            "restoring",
            "restarting",
        }:
            raise OctopError.localized(ErrorCode.AGENT_BUSY)

    workspace = resolve_agent_workspace_dir(server, agent_id)
    row = server.services.agent_repo.get(agent_id)
    cfg: dict[str, Any] = {}
    if row is not None and row.config_json:
        try:
            parsed = json.loads(row.config_json)
            if isinstance(parsed, dict):
                cfg = parsed
        except Exception:
            cfg = {}

    ns, backend, backend_config = open_memory_kwargs(
        agent_id=agent_id,
        cfg=cfg,
        octop_config=server.services.config,
        workspace_dir=workspace,
    )

    result = forget_atom_in_store(
        workspace_dir=workspace,
        namespace=ns,
        atom_id=atom_id,
        backend=backend,
        backend_config=backend_config,
        cfg=cfg,
    )

    if not result.get("found"):
        raise OctopError(ErrorCode.NOT_FOUND, f"memory atom {atom_id!r} not found")

    invalidate_cached_memory(agent_id)
    return result
