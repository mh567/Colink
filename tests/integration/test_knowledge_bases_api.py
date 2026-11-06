"""HTTP ACL for knowledge-base instance settings vs page CRUD."""

from __future__ import annotations

from typing import Any

from tests.support.auth import create_user


async def test_feature_toggle_requires_knowledge_settings(env: Any) -> None:
    client, _server, admin_auth = env
    page_auth = await create_user(client, admin_auth, username="kb_page_user")
    denied = await client.put(
        "/api/knowledge-bases/feature",
        headers=page_auth,
        json={"enabled": False},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "FORBIDDEN"

    settings_auth = await create_user(
        client,
        admin_auth,
        username="kb_settings_user",
        permissions=["knowledge_bases", "knowledge_settings"],
    )
    allowed = await client.put(
        "/api/knowledge-bases/feature",
        headers=settings_auth,
        json={"enabled": False},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["feature_enabled"] is False


async def test_embedding_options_require_knowledge_settings(env: Any) -> None:
    client, _server, admin_auth = env
    page_auth = await create_user(client, admin_auth, username="kb_options_user")
    denied = await client.get("/api/knowledge-bases/embedding-options", headers=page_auth)
    assert denied.status_code == 403, denied.text

    settings_auth = await create_user(
        client,
        admin_auth,
        username="kb_options_admin",
        permissions=["knowledge_settings"],
    )
    allowed = await client.get("/api/knowledge-bases/embedding-options", headers=settings_auth)
    assert allowed.status_code == 200, allowed.text
    assert "onnx" in allowed.json()


async def test_remote_embedding_limits_are_persisted_and_permission_checked(env: Any) -> None:
    client, server, admin_auth = env
    body = {"enabled": False, "embedding_batch_size": 3, "embedding_max_input_chars": 1200}
    response = await client.put("/api/knowledge-bases/feature", headers=admin_auth, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["embedding_batch_size"] == 3
    assert response.json()["embedding_max_input_chars"] == 1200
    capability = await client.get("/api/knowledge-bases/capability", headers=admin_auth)
    assert capability.json()["embedding_batch_size"] == 3
    assert capability.json()["embedding_max_input_chars"] == 1200
    # Fresh capability resolution uses persisted settings, not request-local state.
    from octop.infra.knowledge.gate import get_capability

    reloaded = get_capability(server.services.settings_repo.get, server.services.provider_repo)
    assert reloaded["embedding_batch_size"] == 3
    assert reloaded["embedding_max_input_chars"] == 1200
    denied_auth = await create_user(client, admin_auth, username="kb_limit_reader")
    denied = await client.put("/api/knowledge-bases/feature", headers=denied_auth, json=body)
    assert denied.status_code == 403
    for invalid in (
        {"embedding_batch_size": 0},
        {"embedding_max_input_chars": -1},
        {"embedding_batch_size": True},
    ):
        rejected = await client.put(
            "/api/knowledge-bases/feature", headers=admin_auth, json={**body, **invalid}
        )
        assert rejected.status_code == 422
    unchanged = await client.get("/api/knowledge-bases/capability", headers=admin_auth)
    assert unchanged.json()["embedding_batch_size"] == 3
    assert unchanged.json()["embedding_max_input_chars"] == 1200


async def test_failed_indexing_message_follows_request_language(env: Any) -> None:
    client, server, admin_auth = env
    admin = server.services.user_repo.get_by_username("admin")
    base = server.services.knowledge_repo.create_base(owner_user_id=admin.id, name="Failed docs")
    document = server.services.knowledge_repo.create_document(
        kb_id=base.id,
        filename="failed.txt",
        content_type="text/plain",
        byte_size=1,
    )
    server.services.knowledge_repo.update_document(
        document.id,
        status="failed",
        error_message="knowledge.indexing_failed",
    )
    for language, expected in (("zh", "知识库索引失败"), ("en", "Knowledge indexing failed")):
        response = await client.get(
            f"/api/knowledge-bases/{base.id}/documents",
            headers={**admin_auth, "Accept-Language": language},
        )
        assert response.status_code == 200, response.text
        assert expected in response.json()[0]["error_message"]
    assert (
        server.services.knowledge_repo.get_document(document.id).error_message
        == "knowledge.indexing_failed"
    )
