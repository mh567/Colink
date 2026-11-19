"""Integration tests for personal memory actual forgetting on PostgreSQL (T17).

Verifies that forgetting an atom on PostgreSQL permanently purges the atom,
candidates, raw events, memory tree nodes, and journal records in the
octop_memory schema. Neither soft-deactivation nor expired markers are left behind.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from octop_memory.core import Memory
from psycopg.rows import dict_row

from octop.infra.agents.memory.forget import (
    forget_atom_in_store,
)
from tests.support.postgresql import requires_postgresql


@requires_postgresql
@pytest.mark.postgresql
def test_forget_memory_atom_postgresql(tmp_path: Path):
    dsn = os.environ["OCTOP_TEST_DATABASE_URL"]
    ns = "agent_pg_t17"

    # Clean namespace if leftover from prior test
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS octop_memory")
        for table in (
            "memory_nodes",
            "atoms",
            "candidates",
            "raw_events",
            "journal",
            "entities",
            "aliases",
            "entity_pages",
            "thread_active_entities",
        ):
            cur.execute(
                f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'octop_memory' AND table_name = '{table}') THEN DELETE FROM octop_memory.{table} WHERE namespace = '{ns}'; END IF; END $$;"
            )

    mem = Memory(
        namespace=ns,
        backend="postgres",
        backend_config={"dsn": dsn},
    )

    atom, entity, _ = mem.create_atom(
        "User prefers Postgres 16 partition syntax",
        entity_name="User",
        kind="Preference",
        importance="high",
    )
    atom_id = atom.id
    candidate_id = atom.candidate_id
    raw_event_ids = list(atom.raw_event_ids)

    # 1. Verify atom present in memory queries
    assert mem.search_atoms("partition")
    assert mem.recall("partition")

    # 2. Verify rows exist directly in PostgreSQL
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS c FROM octop_memory.atoms WHERE namespace = %s AND id = %s",
            (ns, atom_id),
        )
        assert cur.fetchone()["c"] == 1
        cur.execute(
            "SELECT count(*) AS c FROM octop_memory.candidates WHERE namespace = %s AND id = %s",
            (ns, candidate_id),
        )
        assert cur.fetchone()["c"] == 1
        cur.execute(
            "SELECT count(*) AS c FROM octop_memory.memory_nodes WHERE namespace = %s AND atom_id = %s",
            (ns, atom_id),
        )
        assert cur.fetchone()["c"] == 1
        cur.execute(
            "SELECT count(*) AS c FROM octop_memory.raw_events WHERE namespace = %s AND id = ANY(%s)",
            (ns, raw_event_ids),
        )
        assert cur.fetchone()["c"] == len(raw_event_ids)

    # 3. Forget the atom
    result = forget_atom_in_store(
        workspace_dir=tmp_path,
        namespace=ns,
        atom_id=atom_id,
        backend="postgres",
        backend_config={"dsn": dsn},
    )
    assert result["forgotten"] is True
    assert result["atom_id"] == atom_id
    assert result["backend"] == "postgres"

    # 4. Direct SQL verification in PostgreSQL: all records physically cleaned up!
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS c FROM octop_memory.atoms WHERE namespace = %s AND id = %s",
            (ns, atom_id),
        )
        assert cur.fetchone()["c"] == 0

        cur.execute(
            "SELECT count(*) AS c FROM octop_memory.candidates WHERE namespace = %s AND id = %s",
            (ns, candidate_id),
        )
        assert cur.fetchone()["c"] == 0

        cur.execute(
            "SELECT count(*) AS c FROM octop_memory.memory_nodes WHERE namespace = %s AND atom_id = %s",
            (ns, atom_id),
        )
        assert cur.fetchone()["c"] == 0

        cur.execute(
            "SELECT count(*) AS c FROM octop_memory.raw_events WHERE namespace = %s AND id = ANY(%s)",
            (ns, raw_event_ids),
        )
        assert cur.fetchone()["c"] == 0

        cur.execute(
            "SELECT count(*) AS c FROM octop_memory.journal WHERE namespace = %s AND (target_atom_id = %s OR target_candidate_id = %s)",
            (ns, atom_id, candidate_id),
        )
        assert cur.fetchone()["c"] == 0

        # PostgreSQL tombstone table recorded
        cur.execute(
            "SELECT count(*) AS c FROM octop_memory.forgotten_memories WHERE namespace = %s AND atom_id = %s",
            (ns, atom_id),
        )
        assert cur.fetchone()["c"] == 1

    # 5. Re-open Memory and verify recall & search return nothing
    mem2 = Memory(
        namespace=ns,
        backend="postgres",
        backend_config={"dsn": dsn},
    )
    assert mem2.search_atoms("partition") == []
    assert mem2.recall("partition") == []
