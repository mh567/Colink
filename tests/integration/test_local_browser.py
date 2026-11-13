"""Browser actions and queries require the employee's own approved native session."""

from __future__ import annotations

from unittest.mock import AsyncMock

from tests.integration import test_devices
from tests.integration.test_local_docx import approved_device, seed_intranet_provider
from tests.support.auth import create_user

env = test_devices.env


async def test_browser_authorizes_only_the_approved_device_with_an_explicit_grant(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice")
    bob = await create_user(client, admin, username="bob")
    alice_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/browser/authorize"
        body = {"action": "open"}
        assert (await client.post(path, headers=native, json=body)).status_code == 403

        granted = await client.patch(
            f"/api/users/{alice_id}", headers=admin, json={"permissions": ["local_browser"]}
        )
        assert granted.status_code == 200
        for action in (
            "open",
            "list",
            "new_tab",
            "select_tab",
            "close_tab",
            "query",
            "capture",
            "close",
        ):
            allowed = await client.post(path, headers=native, json={"action": action})
            assert allowed.status_code == 200
            assert allowed.json() == {"allowed": True}

        assert (await client.post(path, headers=alice, json=body)).status_code == 403
        assert (await client.post(path, headers={**native, **bob}, json=body)).status_code == 403
        async with approved_device(client, admin, alice) as (_other_id, other_native):
            assert (await client.post(path, headers=other_native, json=body)).status_code == 403
        assert (
            await client.post(path, headers=native, json={"action": "evaluate"})
        ).status_code == 422
        assert (
            await client.post(path, headers=native, json={**body, "url": "https://intranet.test"})
        ).status_code == 422

        revoked = await client.patch(
            f"/api/users/{alice_id}", headers=admin, json={"permissions": []}
        )
        assert revoked.status_code == 200
        assert (await client.post(path, headers=native, json=body)).status_code == 403

    async with approved_device(client, admin, admin) as (admin_device_id, admin_native):
        path = f"/api/devices/{admin_device_id}/browser/authorize"
        assert (await client.post(path, headers=admin_native, json=body)).status_code == 403


async def test_browser_query_discards_result_when_device_is_revoked_during_model_call(
    env, monkeypatch
):
    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=["local_browser"])
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    models = server.app_runtime.agent_registry.host_models
    answer = "示例库存为 42 件。依据：页面库存栏显示 42。"
    model = AsyncMock(return_value=answer)
    monkeypatch.setattr(models, "ainvoke_intranet_text", model)

    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/browser/query"
        body = {"instruction": "查询当前库存", "text": "商品 A：库存 42"}
        assert (await client.post(path, headers=alice, json=body)).status_code == 403
        assert (
            await client.post(path, headers=native, json={**body, "url": "https://intranet.test"})
        ).status_code == 422
        assert model.await_count == 0

        response = await client.post(path, headers=native, json=body)
        assert response.status_code == 200
        assert response.json() == {"text": answer}
        assert model.call_args.args[0] == "openai/gpt-4o"
        assert body["instruction"] in model.call_args.args[1]
        assert body["text"] in model.call_args.args[1]
        assert model.call_args.kwargs["pin_ip"] == "10.20.30.40"
        assert "tools" not in model.call_args.kwargs

        async def revoke_device(*args, **kwargs):
            revoked = await client.post(f"/api/devices/{device_id}/revoke", headers=admin)
            revoked.raise_for_status()
            return answer

        model.side_effect = revoke_device
        response = await client.post(path, headers=native, json=body)
        assert response.status_code == 403
        assert answer not in response.text
        assert (await client.post(path, headers=native, json=body)).status_code == 403
        assert model.await_count == 2
