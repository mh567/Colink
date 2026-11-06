"""XLSX tasks enforce their own feature grant at the native HTTP boundary."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.integration import test_devices
from tests.integration.test_local_docx import approved_device, seed_intranet_provider
from tests.support.auth import create_user

env = test_devices.env

GRANTS = ["local_xlsx", "local_files_read", "local_files_create"]
SOURCE = '{"sheets":[{"name":"台账","rows":[["项目","金额"],["甲",12],["乙",8]]}]}'
RESULT = (
    '{"sheets":[{"name":"汇总","rows":[["合计",{"formula":"SUM(B2:B3)"}],["甲",12],["乙",8]]}]}'
)


async def test_xlsx_requires_own_feature_and_native_possession(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    bob = await create_user(client, admin, username="bob", permissions=GRANTS)
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/xlsx/authorize"
        for action in ("read", "create"):
            response = await client.post(path, headers=native, json={"action": action})
            assert response.status_code == 200
            assert response.json() == {"allowed": True}
        for headers in (alice, admin, {**native, **bob}):
            assert (
                await client.post(path, headers=headers, json={"action": "read"})
            ).status_code == 403
        assert (
            await client.post(
                f"/api/devices/{device_id}/docx/authorize", headers=native, json={"action": "read"}
            )
        ).status_code == 403
        async with approved_device(client, admin, alice) as (_other_id, other_native):
            assert (
                await client.post(path, headers=other_native, json={"action": "read"})
            ).status_code == 403
    async with approved_device(client, admin, admin) as (device_id, native):
        assert (
            await client.post(
                f"/api/devices/{device_id}/xlsx/authorize", headers=native, json={"action": "read"}
            )
        ).status_code == 403


async def test_xlsx_generation_returns_structured_data_and_safe_failures(env, monkeypatch):
    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    model = AsyncMock(return_value=RESULT)
    monkeypatch.setattr(
        server.app_runtime.agent_registry.host_models, "ainvoke_intranet_text", model
    )
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/xlsx/generate"
        body = {"instruction": "汇总金额", "text": SOURCE}
        response = await client.post(path, headers=native, json=body)
        assert response.status_code == 200
        assert response.json() == {"text": RESULT}
        assert SOURCE in model.call_args.args[1]
        assert "formula" in model.call_args.kwargs["system"]
        assert "display_rows" in model.call_args.kwargs["system"]
        assert "readable date strings" in model.call_args.kwargs["system"]
        assert "tools" not in model.call_args.kwargs
        model.side_effect = RuntimeError("private-source-and-secret-key")
        response = await client.post(path, headers=native, json=body)
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "LOCAL_XLSX_GENERATION_FAILED"
        assert "private-source" not in response.text
        assert "secret-key" not in response.text


@pytest.mark.parametrize("missing", GRANTS)
async def test_xlsx_rejects_revoked_feature_or_file_action(env, missing):
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
            f"/api/devices/{device_id}/xlsx/generate",
            headers=native,
            json={"instruction": "汇总", "text": SOURCE},
        )
        assert response.status_code == 403


async def test_xlsx_discards_generated_result_after_device_revoked(env, monkeypatch):
    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    async with approved_device(client, admin, alice) as (device_id, native):

        async def generate(*args, **kwargs):
            response = await client.post(f"/api/devices/{device_id}/revoke", headers=admin)
            assert response.status_code == 200
            return RESULT

        monkeypatch.setattr(
            server.app_runtime.agent_registry.host_models, "ainvoke_intranet_text", generate
        )
        response = await client.post(
            f"/api/devices/{device_id}/xlsx/generate",
            headers=native,
            json={"instruction": "汇总", "text": SOURCE},
        )
        assert response.status_code == 403
        assert "汇总" not in response.text


async def test_xlsx_schema_and_explicit_permission_catalog(env):
    from octop.api.app import build_app

    client, server, admin = env
    assert (
        "local_xlsx" not in (await client.get("/api/auth/me", headers=admin)).json()["permissions"]
    )
    schema = build_app(server).openapi()
    for action in ("authorize", "generate"):
        operation = schema["paths"][f"/api/devices/{{device_id}}/xlsx/{action}"]["post"]
        assert operation["summary"]
        assert operation["description"]
        assert "$ref" in operation["responses"]["200"]["content"]["application/json"]["schema"]


@pytest.mark.parametrize("base_url", [None, "https://8.8.8.8/v1"])
async def test_xlsx_never_falls_back_to_missing_or_public_model(env, monkeypatch, base_url):
    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    if base_url:
        await seed_intranet_provider(client, admin, base_url)
        server.services.settings_repo.set_active_model("openai", "gpt-4o")
    model = AsyncMock(return_value=RESULT)
    monkeypatch.setattr(
        server.app_runtime.agent_registry.host_models, "ainvoke_intranet_text", model
    )
    async with approved_device(client, admin, alice) as (device_id, native):
        response = await client.post(
            f"/api/devices/{device_id}/xlsx/generate",
            headers=native,
            json={"instruction": "汇总", "text": SOURCE},
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "LOCAL_XLSX_GENERATION_FAILED"
        model.assert_not_awaited()
