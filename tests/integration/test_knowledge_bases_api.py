"""HTTP ACL for knowledge-base instance settings vs page CRUD."""

from __future__ import annotations

from typing import Any

from tests.support.auth import create_user


async def test_private_base_is_visible_only_to_its_owner(env: Any) -> None:
    client, server, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username="private_kb_owner")
    other_auth = await create_user(client, admin_auth, username="private_kb_other")
    owner = server.services.user_repo.get_by_username("private_kb_owner")
    base = server.services.knowledge_repo.create_base(owner_user_id=owner.id, name="Private")

    owner_list = await client.get("/api/knowledge-bases", headers=owner_auth)
    assert base.id in {item["id"] for item in owner_list.json()}
    assert (
        await client.get(f"/api/knowledge-bases/{base.id}", headers=owner_auth)
    ).status_code == 200

    for auth in (admin_auth, other_auth):
        response = await client.get("/api/knowledge-bases", headers=auth)
        assert base.id not in {item["id"] for item in response.json()}
        assert (
            await client.get(f"/api/knowledge-bases/{base.id}", headers=auth)
        ).status_code == 403


async def test_import_requires_authorized_native_device(env: Any, monkeypatch: Any) -> None:
    from octop.api.routers import knowledge_bases
    from octop.infra.knowledge import service

    client, server, admin_auth = env
    owner_auth = await create_user(
        client,
        admin_auth,
        username="kb_import_owner",
        permissions=["knowledge_bases", "local_files_read"],
    )
    owner = server.services.user_repo.get_by_username("kb_import_owner")
    base = server.services.knowledge_repo.create_base(owner_user_id=owner.id, name="Private")
    monkeypatch.setattr(knowledge_bases, "assert_knowledge_usable", lambda *_: None)
    monkeypatch.setattr(service, "assert_knowledge_usable", lambda *_: None)
    monkeypatch.setattr(knowledge_bases, "enqueue_index_document", lambda *_: None)

    response = await client.post(
        f"/api/knowledge-bases/{base.id}/documents",
        headers=owner_auth,
        files={"upload": ("private.txt", b"private", "text/plain")},
    )
    assert response.status_code == 403
    assert (
        await client.get(f"/api/knowledge-bases/{base.id}/documents", headers=owner_auth)
    ).json() == []

    from tests.integration.test_local_docx import approved_device

    async with approved_device(client, admin_auth, owner_auth) as (device_id, native):
        authorize = f"/api/devices/{device_id}/knowledge/authorize"
        allowed = await client.post(
            authorize,
            headers=native,
            json={"kb_id": base.id, "filename": "private.txt"},
        )
        assert allowed.status_code == 200
        denied = await client.post(
            authorize,
            headers=native,
            json={"kb_id": base.id, "filename": "private.docx"},
        )
        assert denied.status_code == 403
        denied_upload = await client.post(
            f"/api/knowledge-bases/{base.id}/documents",
            headers={**native, "X-Colink-Device-Id": device_id},
            files={"upload": ("private.docx", b"private", "application/octet-stream")},
        )
        assert denied_upload.status_code == 403
        response = await client.post(
            f"/api/knowledge-bases/{base.id}/documents",
            headers={**native, "X-Colink-Device-Id": device_id},
            files={"upload": ("private.txt", b"private", "text/plain")},
        )
        assert response.status_code == 201
        assert response.json()["filename"] == "private.txt"
        denied_other_user = await client.post(
            f"/api/knowledge-bases/{base.id}/documents",
            headers={
                **admin_auth,
                "X-Colink-Device-Id": device_id,
                "X-Colink-Device-Session": native["X-Colink-Device-Session"],
            },
            files={"upload": ("private.txt", b"private", "text/plain")},
        )
        assert denied_other_user.status_code == 403
    disconnected = await client.post(
        f"/api/knowledge-bases/{base.id}/documents",
        headers={**native, "X-Colink-Device-Id": device_id},
        files={"upload": ("private.txt", b"private", "text/plain")},
    )
    assert disconnected.status_code == 403


async def test_import_never_sends_private_text_to_public_embedding(
    env: Any, monkeypatch: Any
) -> None:
    import asyncio

    from octop.api.routers import knowledge_bases
    from octop.infra.knowledge import jobs, service
    from tests.integration.test_local_docx import approved_device

    client, server, admin_auth = env
    owner_auth = await create_user(
        client,
        admin_auth,
        username="kb_network_owner",
        permissions=["knowledge_bases", "local_files_read"],
    )
    owner = server.services.user_repo.get_by_username("kb_network_owner")
    base = server.services.knowledge_repo.create_base(owner_user_id=owner.id, name="Private")
    provider_id = server.services.provider_repo.create(
        name="public-test", kind="openai", base_url="http://8.8.8.8/v1", api_key="secret"
    )
    settings = server.services.settings_repo
    settings.set("knowledge_embedding_backend", "remote")
    settings.set("knowledge_embedding_model", "embed")
    settings.set("knowledge_embedding_provider_id", str(provider_id))
    monkeypatch.setattr(knowledge_bases, "assert_knowledge_usable", lambda *_: None)
    monkeypatch.setattr(service, "assert_knowledge_usable", lambda *_: None)
    monkeypatch.setattr(jobs, "assert_knowledge_usable", lambda *_: None)
    sent = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *args, **kwargs):
            sent.append((args, kwargs))
            raise RuntimeError("public request")

    monkeypatch.setattr("octop.infra.knowledge.embed.httpx.Client", lambda **_: Client())
    async with approved_device(client, admin_auth, owner_auth) as (device_id, native):
        response = await client.post(
            f"/api/knowledge-bases/{base.id}/documents",
            headers={**native, "X-Colink-Device-Id": device_id},
            files={"upload": ("private.txt", b"private content", "text/plain")},
        )
        assert response.status_code == 201
        for _ in range(100):
            listing = await client.get(
                f"/api/knowledge-bases/{base.id}/documents", headers=owner_auth
            )
            if listing.json()[0]["status"] == "failed":
                break
            await asyncio.sleep(0.05)
        assert listing.json()[0]["status"] == "failed"
    assert sent == []


async def test_employee_cannot_publish_private_base(env: Any) -> None:
    client, server, admin_auth = env
    owner_auth = await create_user(
        client, admin_auth, username="kb_publisher", permissions=["knowledge_bases"]
    )
    owner = server.services.user_repo.get_by_username("kb_publisher")
    base = server.services.knowledge_repo.create_base(owner_user_id=owner.id, name="Private")

    for body in ({"shared": True}, {"visibility": "all"}):
        response = await client.patch(
            f"/api/knowledge-bases/{base.id}", headers=owner_auth, json=body
        )
        assert response.status_code == 403
    response = await client.get(f"/api/knowledge-bases/{base.id}", headers=owner_auth)
    assert response.json()["shared"] is False


async def test_group_catalog_does_not_expose_private_bases(env: Any) -> None:
    client, server, admin_auth = env
    await create_user(client, admin_auth, username="kb_group_owner")
    owner = server.services.user_repo.get_by_username("kb_group_owner")
    server.services.knowledge_repo.create_base(owner_user_id=owner.id, name="Private group secret")
    server.services.repos.user_group_repo.create(name="Staff", description="")

    response = await client.get("/api/user-groups", headers=admin_auth)
    assert response.status_code == 200
    assert "Private group secret" not in response.text


async def test_private_document_preview_download_and_removal(env: Any) -> None:
    from octop.infra.knowledge.blobs import KnowledgeBlobs

    client, server, admin_auth = env
    owner_auth = await create_user(
        client, admin_auth, username="kb_doc_owner", permissions=["knowledge_bases"]
    )
    other_auth = await create_user(
        client, admin_auth, username="kb_doc_other", permissions=["knowledge_bases"]
    )
    owner = server.services.user_repo.get_by_username("kb_doc_owner")
    repo = server.services.knowledge_repo
    base = repo.create_base(owner_user_id=owner.id, name="Private")
    doc = repo.create_document(
        kb_id=base.id, filename="sample.txt", content_type="text/plain", byte_size=11
    )
    KnowledgeBlobs.from_services(server.services).write(
        base.id, doc.id, doc.filename, b"private doc"
    )
    url = f"/api/knowledge-bases/{base.id}/documents/{doc.id}"
    assert (await client.get(f"{url}/preview", headers=owner_auth)).json()["text"] == "private doc"
    assert (await client.get(f"{url}/file", headers=owner_auth)).content == b"private doc"
    for auth in (admin_auth, other_auth):
        for suffix in ("preview", "file", "chunks"):
            assert (await client.get(f"{url}/{suffix}", headers=auth)).status_code == 403
        assert (await client.delete(url, headers=auth)).status_code == 403
    assert (await client.delete(url, headers=owner_auth)).status_code == 204
    for suffix in ("preview", "file"):
        assert (await client.get(f"{url}/{suffix}", headers=owner_auth)).status_code == 404


async def test_failed_removal_stays_withdrawn_and_can_be_retried(
    env: Any, monkeypatch: Any
) -> None:
    from octop.infra.knowledge.blobs import KnowledgeBlobs
    from octop.infra.knowledge.index import KnowledgeIndex

    client, server, admin_auth = env
    owner_auth = await create_user(
        client, admin_auth, username="kb_removal_owner", permissions=["knowledge_bases"]
    )
    owner = server.services.user_repo.get_by_username("kb_removal_owner")
    repo = server.services.knowledge_repo
    base = repo.create_base(owner_user_id=owner.id, name="Private")
    doc = repo.create_document(
        kb_id=base.id, filename="sample.txt", content_type="text/plain", byte_size=6, status="ready"
    )
    KnowledgeBlobs.from_services(server.services).write(base.id, doc.id, doc.filename, b"secret")
    url = f"/api/knowledge-bases/{base.id}/documents/{doc.id}"
    original_delete = KnowledgeIndex.delete_doc
    monkeypatch.setattr(
        KnowledgeIndex, "delete_doc", lambda *_args: (_ for _ in ()).throw(OSError("failure"))
    )

    assert (await client.delete(url, headers=owner_auth)).status_code == 500
    listing = await client.get(f"/api/knowledge-bases/{base.id}/documents", headers=owner_auth)
    assert listing.json()[0]["status"] == "withdrawn"
    assert (await client.get(f"{url}/file", headers=owner_auth)).status_code == 404
    assert (await client.get(f"{url}/preview", headers=owner_auth)).status_code == 404

    monkeypatch.setattr(KnowledgeIndex, "delete_doc", original_delete)
    assert (await client.delete(url, headers=owner_auth)).status_code == 204
    assert (await client.get(f"{url}/file", headers=owner_auth)).status_code == 404


async def test_missing_pgvector_makes_knowledge_unavailable(env: Any, monkeypatch: Any) -> None:
    from dataclasses import replace

    from octop.api.routers import knowledge_bases
    from octop.config import DatabaseConfig

    client, server, admin_auth = env
    database = DatabaseConfig(driver="postgresql", host="localhost", database="test", user="test")
    monkeypatch.setattr(
        server,
        "services",
        replace(server.services, config=replace(server.services.config, database=database)),
    )
    monkeypatch.setattr(
        knowledge_bases,
        "get_capability",
        lambda *_: {
            "feature_enabled": True,
            "prerequisites_ok": True,
            "usable": True,
            "checks": {},
        },
    )
    monkeypatch.setattr(knowledge_bases, "assert_knowledge_usable", lambda *_: None)

    import psycopg

    class Connection:
        available = False

        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def execute(self, *_args: object) -> Any:
            return type(
                "Cursor", (), {"fetchone": lambda _self: (1,) if self.available else None}
            )()

    monkeypatch.setattr(psycopg, "connect", lambda *_args, **_kwargs: Connection())

    capability = await client.get("/api/knowledge-bases/capability", headers=admin_auth)
    assert capability.json()["usable"] is False
    assert capability.json()["checks"]["vector_available"] is False
    create = await client.post("/api/knowledge-bases", headers=admin_auth, json={"name": "Private"})
    assert create.status_code == 409
    assert create.json()["error"]["code"] == "KNOWLEDGE_VECTOR_UNAVAILABLE"
    Connection.available = True
    capability = await client.get("/api/knowledge-bases/capability", headers=admin_auth)
    assert capability.json()["usable"] is True
    assert capability.json()["checks"]["vector_available"] is True


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
