"""Unit tests for personal memory actual forgetting (T17).

Verifies physical cleanup of facts, candidates, raw events, memory nodes,
and internal journal entries on SQLite, as well as tombstone persistence
and post-restore pruning.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from octop_memory.core import Memory

from octop.infra.agents.memory.forget import (
    forget_atom_in_store,
    get_forgotten_memories,
    prune_restored_forgotten_memories,
)


def _setup_memory_store(tmp_path: Path, namespace: str = "agent_u1"):
    db_path = tmp_path / "memory.sqlite"
    mem = Memory(
        namespace=namespace,
        backend="sqlite",
        backend_config={"db_path": str(db_path)},
    )
    return mem, db_path


def test_forget_memory_atom_sqlite(tmp_path: Path):
    ns = "agent_u1"
    mem, db_path = _setup_memory_store(tmp_path, namespace=ns)
    atom, entity, _ = mem.create_atom(
        "User prefers dark mode for all desktop tools",
        entity_name="User",
        kind="Preference",
        importance="high",
    )
    atom_id = atom.id
    candidate_id = atom.candidate_id
    raw_event_ids = list(atom.raw_event_ids)

    # Verify present
    assert mem.search_atoms("dark mode")
    assert mem.recall("dark mode")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    assert (
        cur.execute(f"SELECT count(*) FROM {ns}_atoms WHERE id = ?", (atom_id,)).fetchone()[0] == 1
    )
    assert (
        cur.execute(
            f"SELECT count(*) FROM {ns}_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()[0]
        == 1
    )
    assert (
        cur.execute(
            f"SELECT count(*) FROM {ns}_memory_nodes WHERE atom_id = ?", (atom_id,)
        ).fetchone()[0]
        == 1
    )
    for rid in raw_event_ids:
        assert (
            cur.execute(f"SELECT count(*) FROM {ns}_raw_events WHERE id = ?", (rid,)).fetchone()[0]
            == 1
        )
    conn.close()

    # Forget the atom
    result = forget_atom_in_store(
        workspace_dir=tmp_path,
        namespace=ns,
        atom_id=atom_id,
        backend="sqlite",
        backend_config={"db_path": str(db_path)},
    )
    assert result["forgotten"] is True
    assert result["atom_id"] == atom_id

    # Verify physically purged from SQLite
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    assert (
        cur.execute(f"SELECT count(*) FROM {ns}_atoms WHERE id = ?", (atom_id,)).fetchone()[0] == 0
    )
    assert (
        cur.execute(
            f"SELECT count(*) FROM {ns}_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        cur.execute(
            f"SELECT count(*) FROM {ns}_memory_nodes WHERE atom_id = ?", (atom_id,)
        ).fetchone()[0]
        == 0
    )
    for rid in raw_event_ids:
        assert (
            cur.execute(f"SELECT count(*) FROM {ns}_raw_events WHERE id = ?", (rid,)).fetchone()[0]
            == 0
        )
    assert (
        cur.execute(
            f"SELECT count(*) FROM {ns}_journal WHERE target_atom_id = ? OR target_candidate_id = ?",
            (atom_id, candidate_id),
        ).fetchone()[0]
        == 0
    )
    conn.close()

    # Re-open Memory and verify recall & search return nothing
    mem2 = Memory(
        namespace=ns,
        backend="sqlite",
        backend_config={"db_path": str(db_path)},
    )
    assert mem2.search_atoms("dark mode") == []
    assert mem2.recall("dark mode") == []

    # Verify tombstone recorded
    forgotten = get_forgotten_memories(tmp_path, ns)
    assert any(item["atom_id"] == atom_id for item in forgotten)


def test_restore_prunes_forgotten_memories(tmp_path: Path):
    ns = "agent_u2"
    mem, db_path = _setup_memory_store(tmp_path, namespace=ns)
    atom, _, _ = mem.create_atom(
        "Project convention: commit messages in conventional style",
        entity_name="Project",
        kind="Decision",
    )
    atom_id = atom.id

    # Create backup while memory exists
    backup_path = tmp_path / "memory.sqlite.before-slim.12345678123456781234567812345678.bak"
    with sqlite3.connect(db_path) as s, sqlite3.connect(backup_path) as b:
        s.backup(b)

    # Forget the atom
    forget_atom_in_store(
        workspace_dir=tmp_path,
        namespace=ns,
        atom_id=atom_id,
        backend="sqlite",
        backend_config={"db_path": str(db_path)},
    )

    # Simulate restoring the old backup over memory.sqlite
    with sqlite3.connect(backup_path) as b, sqlite3.connect(db_path) as s:
        b.backup(s)

    # Directly in restored SQLite, the atom would be back
    conn = sqlite3.connect(db_path)
    assert (
        conn.execute(f"SELECT count(*) FROM {ns}_atoms WHERE id = ?", (atom_id,)).fetchone()[0] == 1
    )
    conn.close()

    # Prune restored forgotten memories
    pruned_count = prune_restored_forgotten_memories(
        workspace_dir=tmp_path,
        namespace=ns,
        backend="sqlite",
        backend_config={"db_path": str(db_path)},
    )
    assert pruned_count >= 1

    # Now verify the restored memory has been purged
    conn = sqlite3.connect(db_path)
    assert (
        conn.execute(f"SELECT count(*) FROM {ns}_atoms WHERE id = ?", (atom_id,)).fetchone()[0] == 0
    )
    conn.close()
