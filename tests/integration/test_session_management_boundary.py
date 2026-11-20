"""Integration tests for session management and deletion boundaries (Issue #20).

Validates:
1. Employees manage and delete their own sessions from the normal UI/API.
   Other employees and administrators cannot read private sessions by default.
2. Clearly distinguishes session deletion and memory forgetting.
3. Deleting a session does not delete independently saved memories, 90-day
   retained audit logs, or task artifacts on disk (non-simplified cleanup).
4. Error feedback for missing thread, forbidden cross-user access, and active turns.
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest

from octop.infra.audit.file_ops import record_file_event
from octop.infra.db.repos._base import now_ts
from octop.infra.users.permissions import BASELINE_PERMISSIONS
from tests.support.auth import create_user, resolve_user_id


@pytest.fixture
async def env(env_with_provider: Any) -> Any:
    yield env_with_provider


async def _create_test_users(
    client: Any, admin_auth: dict[str, str]
) -> tuple[dict[str, str], dict[str, str], int, int]:
    perms = list(BASELINE_PERMISSIONS) + ["memory"]
    alice_auth = await create_user(client, admin_auth, username="alice_s", permissions=perms)
    bob_auth = await create_user(client, admin_auth, username="bob_s", permissions=perms)
    alice_id = await resolve_user_id(client, admin_auth, "alice_s")
    bob_id = await resolve_user_id(client, admin_auth, "bob_s")
    return alice_auth, bob_auth, alice_id, bob_id


async def test_session_private_scope_across_employees_and_admin(env: Any) -> None:
    """Other employees and administrators cannot read or delete private sessions."""
    client, srv, admin_auth = env
    alice_auth, bob_auth, alice_id, _bob_id = await _create_test_users(client, admin_auth)

    # 1. Create a shared expert
    resp = await client.post(
        "/api/agents/from-expert/default",
        headers=alice_auth,
        json={"name": "shared-workspace-assistant"},
    )
    assert resp.status_code == 201, resp.text
    shared_agent_id = str(resp.json()["agent_id"])
    srv.services.agent_repo.set_shared(shared_agent_id, True)

    # 2. Alice creates two threads on the shared expert
    resp_a1 = await client.post(f"/api/agents/{shared_agent_id}/threads", headers=alice_auth)
    assert resp_a1.status_code == 201, resp_a1.text
    thr_alice_1 = str(resp_a1.json()["thread_id"])

    resp_a2 = await client.post(f"/api/agents/{shared_agent_id}/threads", headers=alice_auth)
    assert resp_a2.status_code == 201, resp_a2.text
    thr_alice_2 = str(resp_a2.json()["thread_id"])

    # 3. Bob creates one thread on the shared expert
    resp_b1 = await client.post(f"/api/agents/{shared_agent_id}/threads", headers=bob_auth)
    assert resp_b1.status_code == 201, resp_b1.text
    thr_bob_1 = str(resp_b1.json()["thread_id"])

    # 4. Alice lists threads on shared expert: sees thr_alice_1 and thr_alice_2, but NOT thr_bob_1
    alice_threads = (
        await client.get(f"/api/agents/{shared_agent_id}/threads", headers=alice_auth)
    ).json()
    alice_thread_ids = {t["thread_id"] for t in alice_threads}
    assert thr_alice_1 in alice_thread_ids
    assert thr_alice_2 in alice_thread_ids
    assert thr_bob_1 not in alice_thread_ids

    # 5. Bob lists threads on shared expert: sees thr_bob_1, but NOT Alice's threads
    bob_threads = (
        await client.get(f"/api/agents/{shared_agent_id}/threads", headers=bob_auth)
    ).json()
    bob_thread_ids = {t["thread_id"] for t in bob_threads}
    assert thr_bob_1 in bob_thread_ids
    assert thr_alice_1 not in bob_thread_ids
    assert thr_alice_2 not in bob_thread_ids

    # 6. Bob tries to access Alice's thread history: 403 Forbidden
    bob_get_history = await client.get(
        f"/api/agents/{shared_agent_id}/threads/{thr_alice_1}/history",
        headers=bob_auth,
    )
    assert bob_get_history.status_code == 403
    assert bob_get_history.json().get("error", {}).get("code") == "FORBIDDEN"

    # 7. Bob tries to delete Alice's thread: 403 Forbidden
    bob_delete = await client.delete(
        f"/api/agents/{shared_agent_id}/threads/{thr_alice_1}",
        headers=bob_auth,
    )
    assert bob_delete.status_code == 403
    assert bob_delete.json().get("error", {}).get("code") == "FORBIDDEN"

    # 8. Admin lists threads by default: only sees admin's own threads, not Alice's or Bob's
    admin_threads = (
        await client.get(f"/api/agents/{shared_agent_id}/threads", headers=admin_auth)
    ).json()
    admin_thread_ids = {t["thread_id"] for t in admin_threads}
    assert thr_alice_1 not in admin_thread_ids
    assert thr_alice_2 not in admin_thread_ids
    assert thr_bob_1 not in admin_thread_ids

    # 9. Admin cannot bypass privacy via ?as_user={alice_id}: 403 Forbidden
    admin_as_user_list = await client.get(
        f"/api/agents/{shared_agent_id}/threads?as_user={alice_id}",
        headers=admin_auth,
    )
    assert admin_as_user_list.status_code == 403
    assert admin_as_user_list.json().get("error", {}).get("code") == "FORBIDDEN"

    # 10. Admin directly accessing Alice's thread: 403 Forbidden
    admin_get_history = await client.get(
        f"/api/agents/{shared_agent_id}/threads/{thr_alice_1}/history",
        headers=admin_auth,
    )
    assert admin_get_history.status_code == 403
    assert admin_get_history.json().get("error", {}).get("code") == "FORBIDDEN"

    # 11. Admin trying to delete Alice's thread: 403 Forbidden
    admin_delete = await client.delete(
        f"/api/agents/{shared_agent_id}/threads/{thr_alice_1}",
        headers=admin_auth,
    )
    assert admin_delete.status_code == 403
    assert admin_delete.json().get("error", {}).get("code") == "FORBIDDEN"

    # 12. Also verify Alice's private expert:
    resp_priv = await client.post(
        "/api/agents/from-expert/default",
        headers=alice_auth,
        json={"name": "alice-private-expert"},
    )
    assert resp_priv.status_code == 201
    private_agent_id = str(resp_priv.json()["agent_id"])

    resp_priv_thr = await client.post(f"/api/agents/{private_agent_id}/threads", headers=alice_auth)
    assert resp_priv_thr.status_code == 201
    thr_priv = str(resp_priv_thr.json()["thread_id"])

    # Bob cannot even access Alice's private expert
    bob_priv_access = await client.get(
        f"/api/agents/{private_agent_id}/threads/{thr_priv}/history",
        headers=bob_auth,
    )
    assert bob_priv_access.status_code == 403


async def test_session_deletion_preserves_task_artifacts_memory_and_audit(env: Any) -> None:
    """Deleting a session removes checkpoints/thread, but preserves artifacts, memory, and audit."""
    client, srv, admin_auth = env
    alice_auth, _bob_auth, _alice_id, _bob_id = await _create_test_users(client, admin_auth)

    # Create a private expert for Alice
    resp = await client.post(
        "/api/agents/from-expert/default",
        headers=alice_auth,
        json={"name": "alice-personal-agent"},
    )
    assert resp.status_code == 201, resp.text
    agent_id = str(resp.json()["agent_id"])

    # Create two threads for Alice
    resp_1 = await client.post(f"/api/agents/{agent_id}/threads", headers=alice_auth)
    assert resp_1.status_code == 201, resp_1.text
    thr_1 = str(resp_1.json()["thread_id"])

    resp_2 = await client.post(f"/api/agents/{agent_id}/threads", headers=alice_auth)
    assert resp_2.status_code == 201, resp_2.text
    thr_2 = str(resp_2.json()["thread_id"])

    # 1. Create a task artifact in the workspace and link it to thr_1
    workspace_dir = srv.app_runtime.agent_registry.resolve_workspace_dir(agent_id)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    artifact_file = workspace_dir / "financial_summary.docx"
    artifact_file.write_bytes(b"PK\x03\x04test-docx-content-bytes")

    srv.app_runtime.gateway.thread_registry.append_artifacts(
        thr_1, ["financial_summary.docx"], agent_id=agent_id
    )

    # Verify the artifact is associated with thr_1 in thread metadata
    thread_data = await client.get(f"/api/agents/{agent_id}/threads", headers=alice_auth)
    thr_1_data = next(t for t in thread_data.json() if t["thread_id"] == thr_1)
    assert any("financial_summary.docx" in a for a in thr_1_data.get("artifacts", []))

    # 2. Create a personal memory atom for Alice on this private agent
    atom_resp = await client.post(
        f"/api/agents/{agent_id}/memory/atoms",
        headers=alice_auth,
        json={
            "assertion": "财务表格格式约定：金额保留两位小数",
            "entity_name": "Alice",
            "entity_type": "User",
        },
    )
    assert atom_resp.status_code == 200, atom_resp.text
    atom_id = atom_resp.json()["atom"]["id"]

    # 3. Record a 90-day retained file audit event
    request_id = "0123456789abcdef0123456789abcdef"
    record_file_event(
        srv.services.audit_repo,
        actor="alice_s",
        action="file.authorized",
        request_id=request_id,
        device_id="dev_colink_client",
        format="docx",
        operation="create",
        path="financial_summary.docx",
        ts=now_ts(),
    )
    audit_rows = srv.services.audit_repo.query(target=request_id)
    assert len(audit_rows) == 1

    # 4. Alice deletes thr_1
    del_resp = await client.delete(
        f"/api/agents/{agent_id}/threads/{thr_1}",
        headers=alice_auth,
    )
    assert del_resp.status_code == 204, del_resp.text

    # 5. Verify the boundaries after deletion:
    # a) thr_1 is removed from thread list, but thr_2 remains
    remaining_threads = (
        await client.get(f"/api/agents/{agent_id}/threads", headers=alice_auth)
    ).json()
    remaining_ids = {t["thread_id"] for t in remaining_threads}
    assert thr_1 not in remaining_ids
    assert thr_2 in remaining_ids

    # b) Task artifact file on disk is NOT deleted (non-simplified cleanup)
    assert artifact_file.is_file()
    assert artifact_file.read_bytes() == b"PK\x03\x04test-docx-content-bytes"

    # c) Personal memory atom is NOT deleted
    search_mem = await client.post(
        f"/api/agents/{agent_id}/memory/atoms/list",
        headers=alice_auth,
        json={"query": "财务表格"},
    )
    assert search_mem.status_code == 200, search_mem.text
    atom_ids = [item["id"] for item in search_mem.json()["items"]]
    assert atom_id in atom_ids

    # d) Retained audit log is NOT deleted
    retained_audit = srv.services.audit_repo.query(target=request_id)
    assert len(retained_audit) == 1
    admin_audit = (
        await client.get(
            f"/api/admin/audit-log?request_id={request_id}",
            headers=admin_auth,
        )
    ).json()
    assert any(row["target"] == request_id for row in admin_audit)


async def test_memory_forget_preserves_sessions_and_audit(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forgetting a memory removes the memory atom, but does not delete sessions or audit."""
    # When octop-memory has not released atom_forget_ids upstream, provide test double.
    from octop_memory.core import Memory

    if not hasattr(Memory, "atom_forget_ids"):
        monkeypatch.setattr(
            Memory,
            "atom_forget_ids",
            lambda self, atom_id: (atom_id,),
            raising=False,
        )
    if not hasattr(Memory, "forget_atom"):

        def _fake_forget_atom(self: Any, atom_id: str) -> bool:
            conn = getattr(self.backend, "_conn", None)
            if conn is not None:
                ns = getattr(self, "namespace", "")
                with conn:
                    conn.execute(f"DELETE FROM {ns}_atoms WHERE id = ?", (atom_id,))
                    with contextlib.suppress(Exception):
                        conn.execute(f"DELETE FROM {ns}_atoms_fts WHERE id = ?", (atom_id,))
            return True

        monkeypatch.setattr(
            Memory,
            "forget_atom",
            _fake_forget_atom,
            raising=False,
        )

    client, srv, admin_auth = env
    alice_auth, _bob_auth, _alice_id, _bob_id = await _create_test_users(client, admin_auth)

    # 1. Create a private expert for Alice
    resp = await client.post(
        "/api/agents/from-expert/default",
        headers=alice_auth,
        json={"name": "alice-report-agent"},
    )
    assert resp.status_code == 201, resp.text
    agent_id = str(resp.json()["agent_id"])

    # 2. Create a thread
    resp = await client.post(f"/api/agents/{agent_id}/threads", headers=alice_auth)
    thr_id = str(resp.json()["thread_id"])

    # 3. Create a memory atom
    atom_resp = await client.post(
        f"/api/agents/{agent_id}/memory/atoms",
        headers=alice_auth,
        json={
            "assertion": "报表抬头：Colink 集团企业专版",
            "entity_name": "Alice",
            "entity_type": "User",
        },
    )
    assert atom_resp.status_code == 200, atom_resp.text
    atom_id = atom_resp.json()["atom"]["id"]

    # 4. Create an audit record
    request_id = "abcdef0123456789abcdef0123456789"
    record_file_event(
        srv.services.audit_repo,
        actor="alice_s",
        action="file.authorized",
        request_id=request_id,
        device_id="dev_colink_client",
        format="docx",
        operation="create",
        path="test_report.docx",
        ts=now_ts(),
    )

    # 5. Explicitly forget the memory atom
    forget_resp = await client.post(
        f"/api/agents/{agent_id}/memory/atoms/{atom_id}:forget",
        headers=alice_auth,
    )
    assert forget_resp.status_code == 200, forget_resp.text
    assert forget_resp.json() == {"atom_id": atom_id, "forgotten": True}

    # 6. Verify the atom is physically gone
    search_mem = await client.post(
        f"/api/agents/{agent_id}/memory/atoms/list",
        headers=alice_auth,
        json={"query": "报表抬头"},
    )
    assert search_mem.status_code == 200
    assert atom_id not in [item["id"] for item in search_mem.json()["items"]]

    # 7. Verify the session was NOT deleted
    user_threads = (await client.get(f"/api/agents/{agent_id}/threads", headers=alice_auth)).json()
    assert any(t["thread_id"] == thr_id for t in user_threads)

    # 8. Verify the audit log was NOT deleted
    retained_audit = srv.services.audit_repo.query(target=request_id)
    assert len(retained_audit) == 1


async def test_session_delete_error_feedback_turn_active_and_missing(env: Any) -> None:
    """Error feedback: 404 for missing thread, 409 for active turn."""
    client, srv, admin_auth = env
    alice_auth, _bob_auth, _alice_id, _bob_id = await _create_test_users(client, admin_auth)

    resp = await client.post(
        "/api/agents/from-expert/default",
        headers=alice_auth,
        json={"name": "alice-test-agent"},
    )
    assert resp.status_code == 201, resp.text
    agent_id = str(resp.json()["agent_id"])

    # 1. 404 Not Found for non-existent thread
    missing_resp = await client.delete(
        f"/api/agents/{agent_id}/threads/thr_nonexistent_999",
        headers=alice_auth,
    )
    assert missing_resp.status_code == 404
    assert missing_resp.json().get("error", {}).get("code") == "AGENT_NOT_FOUND"

    # 2. Create a thread and mark turn active
    resp = await client.post(f"/api/agents/{agent_id}/threads", headers=alice_auth)
    thr_id = str(resp.json()["thread_id"])

    srv.app_runtime.gateway.ws_hub.mark_turn_active(thr_id)
    assert srv.app_runtime.gateway.ws_hub.is_turn_active(thr_id) is True

    # 3. 409 Conflict when turn is active
    active_resp = await client.delete(
        f"/api/agents/{agent_id}/threads/{thr_id}",
        headers=alice_auth,
    )
    assert active_resp.status_code == 409
    body = active_resp.json()
    assert body.get("error", {}).get("code") == "THREAD_TURN_ACTIVE"

    # 4. Clear active turn -> deletion succeeds
    srv.app_runtime.gateway.ws_hub.mark_turn_idle(thr_id)
    assert srv.app_runtime.gateway.ws_hub.is_turn_active(thr_id) is False

    ok_resp = await client.delete(
        f"/api/agents/{agent_id}/threads/{thr_id}",
        headers=alice_auth,
    )
    assert ok_resp.status_code == 204
