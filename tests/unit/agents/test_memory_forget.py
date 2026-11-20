"""Host persistence stores only removal IDs; SQL cleanup is tested in octop-memory."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from octop.infra.agents.memory.personal import forgotten_ids, record_forgotten_ids


def test_removal_ids_survive_reopen_without_memory_content(tmp_path):
    assert forgotten_ids(tmp_path) == {}
    record_forgotten_ids(tmp_path, "Expert_A", ("new-id", "old-id"))
    record_forgotten_ids(tmp_path, "Expert_A", ("new-id",))
    assert {agent: set(ids) for agent, ids in forgotten_ids(tmp_path).items()} == {
        "Expert_A": {"new-id", "old-id"}
    }
    with closing(sqlite3.connect(tmp_path / "memory-forget-ids.sqlite")) as conn:
        assert set(conn.execute("SELECT * FROM forgotten").fetchall()) == {
            ("Expert_A", "old-id"),
            ("Expert_A", "new-id"),
        }
