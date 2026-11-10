"""Exercise presentation tasks at the authenticated native device boundary."""

from unittest.mock import AsyncMock

import httpx
import pytest

from octop.api.app import build_app
from octop.infra.utils import ssrf_guard
from tests.integration import test_devices
from tests.integration.test_local_docx import approved_device, seed_intranet_provider
from tests.support.app import octop_client, write_octop_config
from tests.support.auth import create_user

env = test_devices.env

GRANTS = ["local_pptx", "local_files_read", "local_files_create"]
REPORT = '{"slides":[{"title":"项目汇报","bullets":["已完成 12 项"]}]}'


async def test_pptx_uses_own_grant_and_sends_only_text_to_intranet_model(env, monkeypatch):
    client, server, admin = env
    assert "/api/devices/{device_id}/pptx/generate" in build_app(server).openapi()["paths"]
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    bob = await create_user(client, admin, username="bob", permissions=GRANTS)
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    model = AsyncMock(return_value=REPORT)
    monkeypatch.setattr(
        server.app_runtime.agent_registry.host_models, "ainvoke_intranet_text", model
    )
    async with approved_device(client, admin, alice) as (device_id, native):
        prefix = f"/api/devices/{device_id}/pptx"
        body = {"instruction": "生成项目汇报", "text": "项目进展\n已完成 12 项"}
        for headers in (alice, admin, {**native, **bob}):
            response = await client.post(f"{prefix}/generate", headers=headers, json=body)
            assert response.status_code == 403
        model.assert_not_awaited()
        for action in ("read", "create"):
            response = await client.post(
                f"{prefix}/authorize", headers=native, json={"action": action}
            )
            assert response.status_code == 200
        response = await client.post(f"{prefix}/generate", headers=native, json=body)
        assert response.status_code == 200
        assert response.json() == {"text": REPORT}
        assert "已完成 12 项" in model.call_args.args[1]
        assert "slides" in model.call_args.kwargs["system"]
        assert "tools" not in model.call_args.kwargs
        model.side_effect = RuntimeError("private model error and credentials")
        response = await client.post(f"{prefix}/generate", headers=native, json=body)
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "LOCAL_PPTX_GENERATION_FAILED"
        assert "credentials" not in response.text


@pytest.mark.parametrize("missing", GRANTS)
async def test_pptx_rejects_revoked_grants_before_generation(env, missing):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    user_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    async with approved_device(client, admin, alice) as (device_id, native):
        response = await client.patch(
            f"/api/users/{user_id}",
            headers=admin,
            json={"permissions": [key for key in GRANTS if key != missing]},
        )
        assert response.status_code == 200
        response = await client.post(
            f"/api/devices/{device_id}/pptx/generate",
            headers=native,
            json={"instruction": "生成项目汇报", "text": "已完成 12 项"},
        )
        assert response.status_code == 403


async def test_pptx_rechecks_docx_source_grant_before_and_after_model(env, monkeypatch):
    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    user_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    model = AsyncMock(return_value=REPORT)
    monkeypatch.setattr(
        server.app_runtime.agent_registry.host_models, "ainvoke_intranet_text", model
    )

    async def set_grants(permissions):
        response = await client.patch(
            f"/api/users/{user_id}", headers=admin, json={"permissions": permissions}
        )
        assert response.status_code == 200, response.text

    async with approved_device(client, admin, alice) as (device_id, native):
        prefix = f"/api/devices/{device_id}/pptx"
        body = {"instruction": "生成汇报", "text": "已完成 12 项", "source_format": "docx"}
        check = {"action": "create", "source_format": "docx"}
        response = await client.post(f"{prefix}/generate", headers=native, json=body)
        assert response.status_code == 403, response.text
        model.assert_not_awaited()
        response = await client.post(f"{prefix}/authorize", headers=native, json=check)
        assert response.status_code == 403, response.text

        await set_grants([*GRANTS, "local_docx"])
        response = await client.post(f"{prefix}/generate", headers=native, json=body)
        assert response.status_code == 200, response.text
        assert response.json() == {"text": REPORT}
        response = await client.post(f"{prefix}/authorize", headers=native, json=check)
        assert response.status_code == 200, response.text

        async def revoke_docx(*args, **kwargs):
            await set_grants(GRANTS)
            return REPORT

        model.side_effect = revoke_docx
        response = await client.post(f"{prefix}/generate", headers=native, json=body)
        assert response.status_code == 403, response.text
        assert "slides" not in response.text
        response = await client.post(f"{prefix}/authorize", headers=native, json=check)
        assert response.status_code == 403, response.text


async def test_pptx_expert_uses_only_intranet_knowledge_embedding(env, monkeypatch):
    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=[*GRANTS, "experts"])
    user_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    await seed_intranet_provider(client, admin)
    response = await client.post(
        "/api/admin/providers",
        headers=admin,
        json={
            "name": "report-embeddings",
            "kind": "openai",
            "base_url": "https://8.8.8.8/v1",
            "api_key": "test-key",
            "models": [{"id": "text-embedding-3-small", "name": "Embedding", "enabled": True}],
        },
    )
    assert response.status_code < 300, response.text
    provider = server.services.provider_repo.get_by_name("report-embeddings")
    response = await client.put(
        "/api/knowledge-bases/feature",
        headers=admin,
        json={
            "enabled": True,
            "backend": "remote",
            "provider_id": str(provider.id),
            "model": "text-embedding-3-small",
        },
    )
    assert response.status_code == 200, response.text
    base = server.services.knowledge_repo.create_base(owner_user_id=user_id, name="Project")
    response = await client.post(
        "/api/experts/local",
        headers=alice,
        json={
            "name": "Report expert",
            "default_model": "openai/gpt-4o",
            "actions": ["local_pptx"],
            "knowledge_base_ids": [base.id],
        },
    )
    assert response.status_code == 201, response.text
    agent_id = response.json()["agent_id"]
    model = AsyncMock(return_value=REPORT)
    monkeypatch.setattr(
        server.app_runtime.agent_registry.host_models, "ainvoke_intranet_text", model
    )
    requests = []
    redirect = False

    def respond(request):
        requests.append(request)
        if redirect:
            return httpx.Response(307, headers={"location": "https://8.8.8.8/v1/embeddings"})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    client_factory = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client_factory(transport=httpx.MockTransport(respond), **kwargs),
    )

    def pinned(host, pin_ip, *, strict_host):
        assert (host, pin_ip, strict_host) == ("10.20.30.50", "10.20.30.50", True)
        return httpx.MockTransport(respond)

    monkeypatch.setattr(ssrf_guard, "PinnedIPTransport", pinned)
    async with approved_device(client, admin, alice) as (device_id, native):
        route = f"/api/devices/{device_id}/pptx/generate"
        body = {"instruction": "整理示例项目", "text": "已完成 12 项", "agent_id": agent_id}
        response = await client.post(route, headers=native, json=body)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "LOCAL_PPTX_KNOWLEDGE_UNAVAILABLE"
        assert requests == []
        model.assert_not_awaited()

        response = await client.patch(
            f"/api/admin/providers/{provider.id}",
            headers=admin,
            json={"base_url": "https://10.20.30.50/v1"},
        )
        assert response.status_code == 200, response.text
        response = await client.post(route, headers=native, json=body)
        assert response.status_code == 200, response.text
        assert response.json() == {"text": REPORT}
        assert len(requests) == 1
        assert str(requests[0].url) == "https://10.20.30.50/v1/embeddings"
        assert "整理示例项目" in requests[0].content.decode()
        assert "已完成 12 项" not in requests[0].content.decode()

        requests.clear()
        redirect = True
        response = await client.post(route, headers=native, json=body)
        assert response.status_code == 409, response.text
        assert len(requests) == 1
        assert requests[0].url.host == "10.20.30.50"


async def test_pptx_routes_are_available_in_api_docs(tmp_octop_home):
    write_octop_config(tmp_octop_home, enable_api_docs=True)
    async with octop_client(tmp_octop_home) as (client, _server):
        docs = await client.get("/api/docs")
        assert docs.status_code == 200
        assert "/api/openapi.json" in docs.text
        schema = (await client.get("/api/openapi.json")).json()
    for action in ("authorize", "generate"):
        route = schema["paths"][f"/api/devices/{{device_id}}/pptx/{action}"]["post"]
        assert route["summary"] and route["description"]
        assert route["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        assert route["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]

    authorize = schema["components"]["schemas"]["PptxAuthorizeBody"]["properties"]
    assert authorize["action"]["enum"] == ["read", "create"]
    assert authorize["source_format"]["default"] == "pptx"
    generate = schema["components"]["schemas"]["PptxGenerateBody"]["properties"]
    assert generate["action"]["const"] == "create"
    assert generate["source_format"]["enum"] == ["pptx", "docx"]
