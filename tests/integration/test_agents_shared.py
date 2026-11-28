"""Shared agent list and ownership behavior."""

from __future__ import annotations

import pytest

from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.users.groups import RESOURCE_EXPERT
from tests.support.auth import create_user, resolve_user_id
from tests.support.http import chat_ws_path, ws_chat_turn, ws_connect


@pytest.fixture
async def env(env_with_provider):
    yield env_with_provider


async def _shared_agent(env) -> tuple[object, dict[str, str], dict[str, str], str]:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username="share_owner")
    peer_auth = await create_user(client, admin_auth, username="share_peer")
    response = await client.post(
        "/api/agents/from-expert/default",
        headers=owner_auth,
        json={"name": "shared-bot"},
    )
    assert response.status_code == 201, response.text
    agent_id = response.json()["agent_id"]
    # Compatibility fixture: sharing was possible before reviewed offline releases.
    srv.services.agent_repo.set_shared(agent_id, True)
    return client, owner_auth, peer_auth, agent_id


async def test_shared_agent_appears_for_other_user(env) -> None:
    client, owner_auth, peer_auth, agent_id = await _shared_agent(env)

    response = await client.get("/api/agents", headers=peer_auth)
    assert response.status_code == 200
    agents = {row["agent_id"]: row for row in response.json()}
    assert agent_id in agents
    assert agents[agent_id]["is_owner"] is False
    assert agents[agent_id]["owner_username"] == "share_owner"

    response = await client.get(f"/api/agents/{agent_id}", headers=peer_auth)
    assert response.status_code == 200
    assert response.json()["is_shared"] is True
    assert response.json()["is_owner"] is False


@pytest.mark.parametrize("shared", [True, False], ids=["group-shared", "private"])
async def test_agent_list_respects_owner_and_group_membership_for_all_roles(env, shared):
    client, owner_auth, peer_auth, agent_id = await _shared_agent(env)
    _, srv, admin_auth = env
    repos = srv.services.repos
    group = repos.user_group_repo.create(name="Report team", description=None)
    peer_id = await resolve_user_id(client, admin_auth, "share_peer")
    repos.user_group_member_repo.add(group.user_group_id, peer_id)
    repos.resource_group_grant_repo.replace(RESOURCE_EXPERT, agent_id, [group.user_group_id])
    repos.agent_repo.set_shared(agent_id, shared)

    for auth, visible in ((owner_auth, True), (peer_auth, shared), (admin_auth, False)):
        response = await client.get("/api/agents", headers=auth)
        assert response.status_code == 200, response.text
        listed = {row["agent_id"] for row in response.json()}
        assert (agent_id in listed) is visible


async def test_employee_cannot_create_or_update_directly_shared_agent(env) -> None:
    client, _srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username="shared_create_owner")
    peer_auth = await create_user(client, admin_auth, username="shared_create_peer")

    response = await client.post(
        "/api/agents",
        headers=owner_auth,
        json={"name": "created-shared-bot", "config": {}, "is_shared": True},
    )
    assert response.status_code == 403, response.text
    assert (await client.get("/api/agents", headers=owner_auth)).json() == []
    response = await client.post(
        "/api/agents", headers=owner_auth, json={"name": "private-bot", "config": {}}
    )
    assert response.status_code == 201, response.text
    agent_id = response.json()["agent_id"]
    denied = await client.patch(
        f"/api/agents/{agent_id}", headers=owner_auth, json={"is_shared": True}
    )
    assert denied.status_code == 403, denied.text
    response = await client.get("/api/agents", headers=peer_auth)
    assert response.status_code == 200
    assert agent_id not in {row["agent_id"] for row in response.json()}


async def test_peer_cannot_patch_shared_agent(env) -> None:
    client, owner_auth, peer_auth, agent_id = await _shared_agent(env)

    response = await client.patch(
        f"/api/agents/{agent_id}",
        headers=peer_auth,
        json={"name": "hijack"},
    )
    assert response.status_code == 403


async def test_peer_cannot_mutate_shared_agent_workspace_or_skills(env) -> None:
    client, owner_auth, peer_auth, agent_id = await _shared_agent(env)

    response = await client.put(
        f"/api/agents/{agent_id}/workspace/file",
        headers=peer_auth,
        params={"path": "SOUL.md"},
        json={"content": "peer mutation"},
    )
    assert response.status_code == 403

    response = await client.post(
        f"/api/agents/{agent_id}/skills/example/disable",
        headers=peer_auth,
    )
    assert response.status_code == 403

    response = await client.get(
        f"/api/agents/{agent_id}/skills",
        headers=peer_auth,
    )
    assert response.status_code == 200, response.text

    response = await client.post(
        "/api/mbti/apply",
        headers={**peer_auth, "X-Octop-Agent-Id": agent_id},
        json={"code": "INTJ"},
    )
    assert response.status_code == 403

    response = await client.get(
        f"/api/agents/{agent_id}/terminal/context",
        headers=peer_auth,
    )
    assert response.status_code == 403

    response = await client.get(
        f"/api/agents/{agent_id}/chat/welcome",
        headers=peer_auth,
    )
    assert response.status_code == 200, response.text
    assert "welcome_message" in response.json()

    response = await client.get(f"/api/agents/{agent_id}/threads", headers=peer_auth)
    assert response.status_code == 200, response.text


async def test_shared_agent_does_not_expose_owner_inbound_attachment(env) -> None:
    client, owner_auth, peer_auth, agent_id = await _shared_agent(env)

    response = await client.put(
        f"/api/agents/{agent_id}/workspace/file",
        headers=owner_auth,
        params={"path": "inbound/shared-attachment.txt"},
        json={"content": "shared attachment"},
    )
    assert response.status_code == 200, response.text

    response = await client.get(
        f"/api/agents/{agent_id}/workspace/file",
        headers=owner_auth,
        params={"path": "inbound/shared-attachment.txt"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["content"] == "shared attachment"

    response = await client.get(
        f"/api/agents/{agent_id}/workspace/file",
        headers=peer_auth,
        params={"path": "inbound/shared-attachment.txt"},
    )
    assert response.status_code == 403, response.text

    response = await client.get(
        f"/api/agents/{agent_id}/workspace/download",
        headers=peer_auth,
        params={"path": "inbound/shared-attachment.txt"},
    )
    assert response.status_code == 403, response.text
    assert b"shared attachment" not in response.content


@pytest.mark.parametrize("actor", ["peer", "admin"])
@pytest.mark.parametrize("block_type", ["file", "image", "image_url"])
async def test_shared_agent_rejects_owner_attachment_in_other_users_chat(
    env, actor: str, block_type: str
) -> None:
    client, owner_auth, peer_auth, agent_id = await _shared_agent(env)
    auth = peer_auth if actor == "peer" else env[2]
    uploaded = await client.post(
        f"/api/agents/{agent_id}/upload",
        headers=owner_auth,
        files={"file": ("private.txt", b"owner-only attachment", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text

    async with ws_connect(client._octop_app, chat_ws_path(agent_id, auth)) as ws:
        await ws.send_json(
            {
                "type": "user_turn",
                "text": "Read this attachment",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": block_type,
                                "workspace_path": uploaded.json()["workspace_path"],
                                "filename": "private.txt",
                                "media_type": "text/plain",
                            }
                        ],
                    }
                ],
            }
        )
        frames = await ws.drain_turn()

    assert frames[-1]["type"] == "error", frames
    assert frames[-1]["message"] == "agent not owned by user"
    threads = await client.get(f"/api/agents/{agent_id}/threads", headers=auth)
    assert threads.status_code == 200, threads.text
    assert threads.json() == []


async def test_shared_agent_still_allows_peer_text_chat(env) -> None:
    client, _owner_auth, peer_auth, agent_id = await _shared_agent(env)

    frames = await ws_chat_turn(client, agent_id, peer_auth, text="Hello")

    assert frames[-1]["type"] == "done", frames
    assert all(frame["type"] != "error" for frame in frames)


async def test_unshare_hides_agent_from_peer(env) -> None:
    client, owner_auth, peer_auth, agent_id = await _shared_agent(env)

    response = await client.patch(
        f"/api/agents/{agent_id}",
        headers=owner_auth,
        json={"is_shared": False},
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_shared"] is False

    response = await client.get("/api/agents", headers=peer_auth)
    assert response.status_code == 200
    assert agent_id not in {row["agent_id"] for row in response.json()}

    response = await client.get(f"/api/agents/{agent_id}", headers=peer_auth)
    assert response.status_code == 403


async def test_shared_agent_threads_are_private(env) -> None:
    client, owner_auth, peer_auth, agent_id = await _shared_agent(env)

    response = await client.post(f"/api/agents/{agent_id}/threads", headers=owner_auth)
    assert response.status_code == 201, response.text
    owner_thread_id = response.json()["thread_id"]

    response = await client.post(f"/api/agents/{agent_id}/threads", headers=peer_auth)
    assert response.status_code == 201, response.text
    peer_thread_id = response.json()["thread_id"]

    response = await client.get(f"/api/agents/{agent_id}/threads", headers=peer_auth)
    assert response.status_code == 200, response.text
    assert {thread["thread_id"] for thread in response.json()} == {peer_thread_id}

    response = await client.get(
        f"/api/agents/{agent_id}/threads/{owner_thread_id}/history",
        headers=peer_auth,
    )
    assert response.status_code == 403


async def test_shared_viewer_marks_own_unread_read(env) -> None:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username="read_owner")
    peer_auth = await create_user(client, admin_auth, username="read_peer")
    response = await client.post(
        "/api/agents/from-expert/default",
        headers=owner_auth,
        json={"name": "read-bot"},
    )
    assert response.status_code == 201, response.text
    agent_id = response.json()["agent_id"]
    owner_id = await resolve_user_id(client, admin_auth, "read_owner")
    peer_id = await resolve_user_id(client, admin_auth, "read_peer")
    sessions = srv.services.session_repo
    for user_id, label in ((owner_id, "owner"), (peer_id, "peer")):
        session_key = ThreadRegistry.dashboard_key(agent_id=agent_id, user_id=user_id)
        sessions.upsert(
            session_key=session_key,
            agent_id=agent_id,
            user_id=user_id,
            channel_type="dashboard",
            chat_type="dm",
            thread_id=f"thr_{label}",
        )
        sessions.increment_unread(session_key, delta=2)

    denied = await client.post(f"/api/agents/{agent_id}/read", headers=peer_auth)
    assert denied.status_code == 403

    srv.services.agent_repo.set_shared(agent_id, True)
    cleared = await client.post(f"/api/agents/{agent_id}/read", headers=peer_auth)
    assert cleared.status_code == 204, cleared.text

    response = await client.get("/api/agents", headers=peer_auth)
    assert response.status_code == 200
    peer_row = next(row for row in response.json() if row["agent_id"] == agent_id)
    assert peer_row["unread_count"] == 0

    response = await client.get("/api/agents", headers=owner_auth)
    assert response.status_code == 200
    owner_row = next(row for row in response.json() if row["agent_id"] == agent_id)
    assert owner_row["unread_count"] == 2


async def test_peer_cannot_rebind_session_to_owner_thread(env) -> None:
    client, owner_auth, peer_auth, agent_id = await _shared_agent(env)

    response = await client.post(f"/api/agents/{agent_id}/threads", headers=owner_auth)
    assert response.status_code == 201, response.text
    owner_thread_id = response.json()["thread_id"]

    response = await client.patch(
        f"/api/agents/{agent_id}/session",
        headers=peer_auth,
        json={"thread_id": owner_thread_id},
    )
    assert response.status_code == 403
