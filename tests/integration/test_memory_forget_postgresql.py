"""Exercise owner forgetting through Colink with a real PostgreSQL memory store."""

from __future__ import annotations

import json
import os
from uuid import uuid4

import psycopg
import pytest

from octop.infra.agents.memory.personal import forgotten_ids
from tests.support.postgresql import requires_postgresql


@requires_postgresql
@pytest.mark.postgresql
@pytest.mark.asyncio
async def test_forget_postgres_with_generated_agent_id(env_with_main_agent):
    client, srv, auth, main_id = env_with_main_agent
    dsn = os.environ["OCTOP_TEST_DATABASE_URL"]
    agent_id = "Expert_" + uuid4().hex[:12].upper()
    ns = f"agent_{agent_id.lower()}"
    repo = srv.services.agent_repo
    repo.create(
        agent_id=agent_id,
        user_id=repo.get(main_id).user_id,
        name="PostgreSQL memory test",
        config_json=json.dumps({"memory": {"backend": {"type": "postgres", "dsn": dsn}}}),
    )
    url = f"/api/agents/{agent_id}/memory/atoms"
    create = await client.post(
        url,
        headers=auth,
        json={
            "assertion": "Partition naming convention",
            "entity_name": "User",
            "entity_type": "User",
        },
    )
    assert create.status_code == 200, create.text
    atom_id = create.json()["atom"]["id"]
    with psycopg.connect(dsn) as conn:
        before = conn.execute(
            "SELECT candidate_id, raw_event_ids FROM octop_memory.atoms WHERE namespace=%s AND id=%s",
            (ns, atom_id),
        ).fetchone()
    assert before and before[1]
    candidate_id, raw_ids = before
    deleted = await client.post(f"{url}/{atom_id}:forget", headers=auth)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"atom_id": atom_id, "forgotten": True}
    assert forgotten_ids(srv.paths.root) == {agent_id: [atom_id]}
    with psycopg.connect(dsn) as conn:
        for table, column, values in (
            ("atoms", "id", [atom_id]),
            ("candidates", "id", [candidate_id]),
            ("raw_events", "id", raw_ids),
            ("journal", "target_atom_id", [atom_id]),
            ("memory_nodes", "atom_id", [atom_id]),
        ):
            assert conn.execute(
                f"SELECT count(*) FROM octop_memory.{table} WHERE namespace=%s AND {column}=ANY(%s)",
                (ns, values),
            ).fetchone() == (0,)
    search = await client.post(f"{url}/list", headers=auth, json={"query": "Partition"})
    assert search.json()["items"] == []
