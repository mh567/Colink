"""Browser actions and queries require the employee's own approved native session."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from tests.integration import test_devices
from tests.integration.test_local_docx import approved_device, seed_intranet_provider
from tests.support.auth import create_user

env = test_devices.env
FORM_FIELDS = [
    {
        "id": "field-1",
        "label": "备注",
        "type": "textarea",
        "value": "page-input-example",
        "options": [],
    },
    {"id": "field-2", "label": "确认", "type": "checkbox", "value": True, "options": []},
    {
        "id": "field-3",
        "label": "优先级",
        "type": "select",
        "value": "normal",
        "options": [{"label": "普通", "value": "normal"}, {"label": "紧急", "value": "urgent"}],
    },
]
FORM_BODY = {"instruction": "将优先级设为紧急并取消确认", "fields": FORM_FIELDS}


async def test_browser_authorizes_only_the_approved_device_with_an_explicit_grant(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice")
    bob = await create_user(client, admin, username="bob")
    alice_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/browser/authorize"
        form_actions = ("forms", "suggest_form", "submit_form", "operations")
        body = {"action": "open"}
        for action in ("open", *form_actions):
            assert (
                await client.post(path, headers=native, json={"action": action})
            ).status_code == 403

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
            *form_actions,
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
        for action in ("open", *form_actions):
            assert (
                await client.post(path, headers=native, json={"action": action})
            ).status_code == 403

    async with approved_device(client, admin, admin) as (admin_device_id, admin_native):
        path = f"/api/devices/{admin_device_id}/browser/authorize"
        assert (await client.post(path, headers=admin_native, json=body)).status_code == 403


async def test_browser_authorizes_upload_and_download_with_file_permissions(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=["local_browser"])
    alice_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/browser/authorize"

        # Only local_browser: upload and download are forbidden
        assert (
            await client.post(path, headers=native, json={"action": "upload"})
        ).status_code == 403
        assert (
            await client.post(path, headers=native, json={"action": "download"})
        ).status_code == 403
        assert (
            await client.post(path, headers=native, json={"action": "overwrite"})
        ).status_code == 403

        # Add local_files_read: upload is allowed, download and overwrite forbidden
        await client.patch(
            f"/api/users/{alice_id}",
            headers=admin,
            json={"permissions": ["local_browser", "local_files_read"]},
        )
        allowed = await client.post(path, headers=native, json={"action": "upload"})
        assert allowed.status_code == 200
        assert allowed.json() == {"allowed": True}
        assert (
            await client.post(path, headers=native, json={"action": "download"})
        ).status_code == 403
        assert (
            await client.post(path, headers=native, json={"action": "overwrite"})
        ).status_code == 403

        # Add local_files_create: download is allowed
        await client.patch(
            f"/api/users/{alice_id}",
            headers=admin,
            json={"permissions": ["local_browser", "local_files_read", "local_files_create"]},
        )
        allowed = await client.post(path, headers=native, json={"action": "download"})
        assert allowed.status_code == 200
        assert allowed.json() == {"allowed": True}
        assert (
            await client.post(path, headers=native, json={"action": "overwrite"})
        ).status_code == 403

        # Add local_files_overwrite: overwrite is allowed
        await client.patch(
            f"/api/users/{alice_id}",
            headers=admin,
            json={
                "permissions": [
                    "local_browser",
                    "local_files_read",
                    "local_files_create",
                    "local_files_overwrite",
                ]
            },
        )
        allowed = await client.post(path, headers=native, json={"action": "overwrite"})
        assert allowed.status_code == 200
        assert allowed.json() == {"allowed": True}

        # Overwriting a browser artifact still requires permission to save the download.
        await client.patch(
            f"/api/users/{alice_id}",
            headers=admin,
            json={"permissions": ["local_browser", "local_files_overwrite"]},
        )
        assert (
            await client.post(path, headers=native, json={"action": "overwrite"})
        ).status_code == 403

        # Revoke local_browser: all fail even if file permissions are kept
        await client.patch(
            f"/api/users/{alice_id}",
            headers=admin,
            json={
                "permissions": ["local_files_read", "local_files_create", "local_files_overwrite"]
            },
        )
        assert (
            await client.post(path, headers=native, json={"action": "upload"})
        ).status_code == 403
        assert (
            await client.post(path, headers=native, json={"action": "download"})
        ).status_code == 403
        assert (
            await client.post(path, headers=native, json={"action": "overwrite"})
        ).status_code == 403


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


@pytest.fixture
async def browser_form_model(env, monkeypatch):
    client, server, admin = env
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    model = AsyncMock(return_value='{"values": {"field-2": false, "field-3": "urgent"}}')
    monkeypatch.setattr(
        server.app_runtime.agent_registry.host_models, "ainvoke_intranet_text", model
    )
    return model


async def test_browser_form_suggestion_requires_native_grant(env, browser_form_model):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice")
    bob = await create_user(client, admin, username="bob", permissions=["local_browser"])
    alice_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/browser/form-suggest"
        response = await client.post(path, headers=native, json=FORM_BODY)
        assert response.status_code == 403
        granted = await client.patch(
            f"/api/users/{alice_id}", headers=admin, json={"permissions": ["local_browser"]}
        )
        granted.raise_for_status()
        for headers in (alice, {**native, **bob}):
            response = await client.post(path, headers=headers, json=FORM_BODY)
            assert response.status_code == 403
        assert browser_form_model.await_count == 0
    response = await client.post(path, headers=native, json=FORM_BODY)
    assert response.status_code == 403
    assert browser_form_model.await_count == 0


async def test_browser_form_suggestion_validates_model_fields_and_options(env, browser_form_model):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=["local_browser"])
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/browser/form-suggest"
        invalid_requests = (
            {**FORM_BODY, "url": "https://intranet.test"},
            {**FORM_BODY, "fields": [{**FORM_FIELDS[0], "selector": "#input"}]},
            {**FORM_BODY, "fields": [{**FORM_FIELDS[0], "type": "password"}]},
            {**FORM_BODY, "fields": [{**FORM_FIELDS[0], "type": "checkbox"}]},
            {**FORM_BODY, "fields": [FORM_FIELDS[0], FORM_FIELDS[0]]},
            {**FORM_BODY, "fields": [{**FORM_FIELDS[0], "value": "a" * (16 * 1024)}]},
        )
        for invalid in invalid_requests:
            response = await client.post(path, headers=native, json=invalid)
            assert response.status_code == 422
            assert "page-input-example" not in response.text
        assert browser_form_model.await_count == 0

        response = await client.post(path, headers=native, json=FORM_BODY)
        assert response.status_code == 200
        assert response.json() == {"values": {"field-2": False, "field-3": "urgent"}}
        call = browser_form_model.call_args
        assert call.args[0] == "openai/gpt-4o"
        assert FORM_BODY["instruction"] in call.args[1]
        assert FORM_FIELDS[0]["value"] in call.args[1]
        assert "untrusted" in call.kwargs["system"]
        assert call.kwargs["pin_ip"] == "10.20.30.40"
        assert "tools" not in call.kwargs

        model_text = "provider-output-example"
        invalid_results = (
            model_text,
            '{"values": {}}',
            '{"values": ["urgent"]}',
            json.dumps({"values": {"unknown": model_text, "field-3": "urgent"}}),
            json.dumps({"values": {"field-3": model_text}}),
            '{"values": {"field-2": "false"}}',
            '{"values": {"field-2": 0}}',
            '{"values": {"field-1": true}}',
            '{"values": {"field-3": "normal", "field-3": "urgent"}}',
            '{"values": {"field-3": "urgent"}, "extra": true}',
        )
        for invalid in invalid_results:
            browser_form_model.return_value = invalid
            response = await client.post(path, headers=native, json=FORM_BODY)
            assert response.status_code == 500
            assert response.json()["error"]["code"] == "LOCAL_BROWSER_QUERY_FAILED"
            assert "values" not in response.json()
            assert model_text not in response.text
            assert "page-input-example" not in response.text

        browser_form_model.side_effect = RuntimeError(model_text)
        response = await client.post(path, headers=native, json=FORM_BODY)
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "LOCAL_BROWSER_QUERY_FAILED"
        assert model_text not in response.text


@pytest.mark.parametrize("revoked_resource", ["device", "model"])
async def test_browser_form_suggestion_discards_revoked_result(
    env, browser_form_model, revoked_resource
):
    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=["local_browser"])
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/browser/form-suggest"
        suggested = "discarded-result-example"

        async def revoke(*args, **kwargs):
            if revoked_resource == "device":
                response = await client.post(f"/api/devices/{device_id}/revoke", headers=admin)
                response.raise_for_status()
            else:
                server.services.settings_repo.set_active_model("openai", "other-model")
            return json.dumps({"values": {"field-1": suggested}})

        browser_form_model.side_effect = revoke
        response = await client.post(path, headers=native, json=FORM_BODY)
        assert response.status_code == 403
        assert suggested not in response.text
        assert "values" not in response.json()
        assert browser_form_model.await_count == 1
