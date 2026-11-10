"""Exercise presentation tasks at the authenticated native device boundary."""

from unittest.mock import AsyncMock

import pytest

from octop.api.app import build_app
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
