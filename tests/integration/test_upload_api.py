"""Integration tests for dashboard chat attachments in agent workspace."""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.support.auth import resolve_user_id
from tests.support.http import chat_ws_path, ws_connect


async def test_upload_and_workspace_download(env_with_agent: Any) -> None:
    client, _srv, auth, aid = env_with_agent
    files = {"file": ("note.txt", b"hello attachment", "text/plain")}
    r = await client.post(f"/api/agents/{aid}/upload", files=files, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == body["workspace_path"]
    assert body["access_url"].startswith(f"/api/agents/{aid}/workspace/download")
    assert body["filename"] == "note.txt"
    assert body["media_type"] == "text/plain"
    assert body["workspace_path"].endswith(".txt")
    assert body["workspace_path"].startswith("inbound/")
    assert re.search(r"inbound/\d{10,}_note\.txt$", body["workspace_path"])
    assert "file_id" not in body
    assert "legacy_url" not in body

    r2 = await client.get(body["access_url"], headers=auth)
    assert r2.status_code == 200, r2.text
    assert r2.content == b"hello attachment"


async def test_upload_pdf_workspace_path(env_with_agent: Any) -> None:
    client, _srv, auth, aid = env_with_agent
    files = {"file": ("report.pdf", b"%PDF-1.4", "application/pdf")}
    r = await client.post(f"/api/agents/{aid}/upload", files=files, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspace_path"].endswith(".pdf")
    assert body["filename"] == "report.pdf"

    r2 = await client.get(body["access_url"], headers=auth)
    assert r2.status_code == 200, r2.text
    assert r2.content == b"%PDF-1.4"


async def test_upload_requires_auth(env: Any) -> None:
    client, _srv, _auth = env
    files = {"file": ("note.txt", b"x", "text/plain")}
    r = await client.post("/api/agents/NOPE/upload", files=files)
    assert r.status_code == 401, r.text


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize(
    ("filename", "content", "media_type"),
    [
        ("private.txt", b"employee-only attachment", "text/plain"),
        ("private.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml"),
    ],
)
async def test_attachment_upload_preview_and_download_require_owner(
    env_alice_bob_agent: Any,
    env_with_provider: Any,
    shared: bool,
    filename: str,
    content: bytes,
    media_type: str,
) -> None:
    client, srv, owner_auth, peer_auth, aid = env_alice_bob_agent
    admin_auth = env_with_provider[2]
    if shared:
        # Legacy shared experts retain the owner's private workspace.
        srv.services.agent_repo.set_shared(aid, True)
    files = {"file": (filename, content, media_type)}
    uploaded = await client.post(f"/api/agents/{aid}/upload", files=files, headers=owner_auth)
    assert uploaded.status_code == 200, uploaded.text
    attachment = uploaded.json()

    downloaded = await client.get(attachment["access_url"], headers=owner_auth)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == content
    unauthenticated = await client.get(attachment["access_url"])
    assert unauthenticated.status_code == 401

    for auth in (peer_auth, admin_auth):
        denied = await client.get(attachment["access_url"], headers=auth)
        assert denied.status_code == 403, denied.text
        assert content not in denied.content
        denied = await client.post(f"/api/agents/{aid}/upload", files=files, headers=auth)
        assert denied.status_code == 403, denied.text
        download = await client.get(
            f"/api/agents/{aid}/workspace/download",
            headers=auth,
            params={"path": attachment["workspace_path"]},
        )
        assert download.status_code == 403, download.text


async def test_chat_attachment_does_not_automatically_enter_knowledge_or_memory(
    env_with_agent: Any,
) -> None:
    client, srv, auth, aid = env_with_agent
    owner_id = await resolve_user_id(client, auth, "admin")
    base = srv.services.knowledge_repo.create_base(
        owner_user_id=owner_id,
        name="Selected knowledge base",
        default_open=True,
    )
    uploaded = await client.post(
        f"/api/agents/{aid}/upload",
        headers=auth,
        files={
            "file": ("private.txt", b"Use this only for the current conversation", "text/plain")
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    attachment = uploaded.json()

    config = await client.get(f"/api/agents/{aid}/memory/extract-config", headers=auth)
    assert config.status_code == 200, config.text
    assert config.json()["capture_enabled"] is False
    assert config.json()["extract_on_session_end"] is False

    async with ws_connect(client._octop_app, chat_ws_path(aid, auth)) as ws:
        await ws.send_json(
            {
                "type": "user_turn",
                "text": "Summarize the attached file",
                "knowledge_base_ids": [base.id],
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "file",
                                "workspace_path": attachment["workspace_path"],
                                "filename": "private.txt",
                                "media_type": "text/plain",
                            }
                        ],
                    }
                ],
            }
        )
        frames = await ws.drain_turn()
    assert frames[-1]["type"] == "done", frames
    assert all(frame["type"] != "error" for frame in frames)

    history = await client.get(
        f"/api/agents/{aid}/threads/{frames[-1]['thread_id']}/history",
        headers=auth,
    )
    assert history.status_code == 200, history.text
    assert attachment["workspace_path"] in history.text

    documents = await client.get(f"/api/knowledge-bases/{base.id}/documents", headers=auth)
    assert documents.status_code == 200, documents.text
    assert documents.json() == []
    bases = await client.get("/api/knowledge-bases", headers=auth)
    assert bases.status_code == 200, bases.text
    assert {item["id"] for item in bases.json()} == {base.id}
    for collection in ("raw_events", "atoms"):
        memories = await client.post(
            f"/api/agents/{aid}/memory/{collection}/list", headers=auth, json={}
        )
        assert memories.status_code == 200, memories.text
        assert memories.json()["items"] == []
