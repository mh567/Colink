"""Owner-only forgetting and restore protection outside any memory backup."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from contextlib import closing, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

from octop.infra.agents.memory.client import (
    _open_memory_for_agent,
    authorize_memory,
    invalidate_cached_memory,
    memory_io_lock,
)
from octop.infra.errors import ErrorCode, OctopError

if TYPE_CHECKING:
    from octop.infra.agents.manager import AgentManager
    from octop.infra.users.identity import User


def memory_restore_marker(root: Path) -> Path:
    return root / "memory-restore-incomplete"


def memory_restore_pending(root: Path) -> bool:
    return memory_restore_marker(root).exists()


def _ledger(root: Path) -> Path:
    return root / "memory-forget-ids.sqlite"


def forgotten_ids(root: Path) -> dict[str, list[str]]:
    path = _ledger(root)
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        return _read_ids(conn)


def _read_ids(conn: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for agent_id, atom_id in conn.execute("SELECT agent_id, atom_id FROM forgotten"):
        out.setdefault(agent_id, []).append(atom_id)
    return out


def record_forgotten_ids(root: Path, agent_id: str, atom_ids: tuple[str, ...]) -> None:
    # This database is not part of either the system archive or the agent store.
    with closing(sqlite3.connect(_ledger(root))) as conn, conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS forgotten (agent_id TEXT, atom_id TEXT, "
            "PRIMARY KEY (agent_id, atom_id))"
        )
        conn.executemany(
            "INSERT OR IGNORE INTO forgotten VALUES (?, ?)",
            ((agent_id, atom_id) for atom_id in atom_ids),
        )


def prune_restored_memories(
    namespace: str, backend: str, backend_config: dict[str, Any] | None, atom_ids: Sequence[str]
) -> None:
    if not atom_ids:
        return
    from octop_memory.core import Memory

    if backend == "sqlite" and not Path((backend_config or {})["db_path"]).is_file():
        return
    memory = Memory(namespace=namespace, backend=backend, backend_config=backend_config)
    try:
        for atom_id in atom_ids:
            memory.forget_atom(atom_id)
    finally:
        close = getattr(memory.backend, "close", None)
        if close is not None:
            close()


async def forget_memory_atom_for_agent(
    registry: AgentManager, agent_id: str, atom_id: str, *, user: User
) -> None:
    await asyncio.to_thread(authorize_memory, registry, agent_id, user)
    if not registry.try_begin_history_backfill(agent_id):
        raise OctopError.localized(ErrorCode.AGENT_BUSY)
    try:
        async with registry._lifecycle_lock_for(agent_id):
            live_agent = registry._octop_harness_or_none(agent_id)
            worker = asyncio.create_task(
                asyncio.to_thread(_forget, registry, agent_id, atom_id, user, live_agent)
            )
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                await worker
                raise
    finally:
        registry.end_history_backfill(agent_id)


def _forget(registry: AgentManager, agent_id: str, atom_id: str, user: User, live: Any) -> None:
    runtime = getattr(live, "_memory_runtime", None) if live is not None else None
    middleware = getattr(runtime, "_middleware", None)
    pause = middleware.pause_background(timeout=120) if middleware is not None else nullcontext()
    with pause, memory_io_lock:
        row = authorize_memory(registry, agent_id, user)
        memory, _bridge = _open_memory_for_agent(registry, row)
        find_ids = getattr(memory, "atom_forget_ids", None)
        forget = getattr(memory, "forget_atom", None)
        if not callable(find_ids) or not callable(forget):
            raise RuntimeError("octop-memory does not provide permanent atom forgetting")
        atom_ids = find_ids(atom_id)
        if not atom_ids:
            raise OctopError.localized(ErrorCode.NOT_FOUND)
        record_forgotten_ids(registry.paths.root, agent_id, atom_ids)
        if not forget(atom_id):
            raise OctopError.localized(ErrorCode.NOT_FOUND)
    invalidate_cached_memory(agent_id)
