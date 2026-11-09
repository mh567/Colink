"""Local DOCX rights are enforced at the native-session HTTP boundary."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.integration import test_devices
from tests.integration.test_devices import _hello
from tests.support.auth import create_user
from tests.support.http import ws_connect

GRANTS = ["local_docx", "local_files_read", "local_files_create"]
env = test_devices.env


async def seed_intranet_provider(client, admin, base_url="http://10.20.30.40/v1"):
    response = await client.post(
        "/api/admin/providers",
        headers=admin,
        json={
            "name": "openai",
            "kind": "openai",
            "base_url": base_url,
            "api_key": "test-key",
            "models": [{"id": "gpt-4o", "name": "Model", "enabled": True, "input": ["text"]}],
        },
    )
    response.raise_for_status()


@asynccontextmanager
async def approved_device(client, admin, owner):
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        state = await _hello(ws, owner, Ed25519PrivateKey.generate())
        assert "session_token" not in state
        device_id = state["device_id"]
        response = await client.post(f"/api/devices/{device_id}/approve", headers=admin)
        assert response.status_code == 200
        await ws.send_json({"type": "heartbeat"})
        state = await ws.receive_json()
        yield device_id, {**owner, "X-Colink-Device-Session": state["session_token"]}


async def test_docx_requires_explicit_feature_action_and_native_session(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/docx/authorize"
        for action in ("read", "create"):
            result = await client.post(path, headers=native, json={"action": action})
            assert result.status_code == 200
            assert result.json() == {"allowed": True}
            assert (
                await client.post(path, headers=alice, json={"action": action})
            ).status_code == 403
        # Neither list nor management endpoints expose the native secret.
        assert "session_token" not in (await client.get("/api/devices", headers=alice)).text
        assert "session_token" not in (await client.get("/api/devices/manage", headers=admin)).text
        assert (
            await client.post(path, headers=native, json={"action": "execute"})
        ).status_code == 422
    assert (await client.post(path, headers=native, json={"action": "read"})).status_code == 403


@pytest.mark.parametrize("format", ["docx", "xlsx"])
@pytest.mark.parametrize("action", ["overwrite", "delete"])
async def test_destructive_file_authorization_requires_the_current_employee_and_action(
    env, format, action
):
    client, _server, admin = env
    grants = [f"local_{format}", "local_files_read", "local_files_create"]
    alice = await create_user(client, admin, username="alice", permissions=grants)
    user_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/{format}/authorize"
        body = {"action": action}
        assert (await client.post(path, headers=native, json=body)).status_code == 403

        changed = await client.patch(
            f"/api/users/{user_id}",
            headers=admin,
            json={"permissions": [*grants, f"local_files_{action}"]},
        )
        assert changed.status_code == 200
        authorized = await client.post(path, headers=native, json=body)
        assert authorized.status_code == 200
        assert authorized.json() == {"allowed": True}
        assert (await client.post(path, headers=alice, json=body)).status_code == 403
        assert (await client.post(path, headers={**native, **admin}, json=body)).status_code == 403
        different_action = "delete" if action == "overwrite" else "overwrite"
        assert (
            await client.post(path, headers=native, json={"action": different_action})
        ).status_code == 403

        changed = await client.patch(
            f"/api/users/{user_id}", headers=admin, json={"permissions": grants}
        )
        assert changed.status_code == 200
        assert (await client.post(path, headers=native, json=body)).status_code == 403


@pytest.mark.parametrize("format", ["docx", "xlsx"])
async def test_overwrite_generation_checks_overwrite_instead_of_create_and_rechecks_after_model(
    env, monkeypatch, format
):
    client, server, admin = env
    grants = [f"local_{format}", "local_files_read"]
    alice = await create_user(client, admin, username="alice", permissions=grants)
    user_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    server.services.user_repo.set_permissions(user_id, [*grants, "local_files_overwrite"])
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    generated = '{"sheets":[{"name":"Summary","rows":[[1]]}]}' if format == "xlsx" else "新正文"
    models = server.app_runtime.agent_registry.host_models

    async def generate(*args, **kwargs):
        return generated

    async def revoke_then_generate(*args, **kwargs):
        server.services.user_repo.set_permissions(user_id, grants)
        return generated

    monkeypatch.setattr(models, "ainvoke_intranet_text", generate)
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/{format}/generate"
        body = {"instruction": "整理", "text": "资料"}
        assert (await client.post(path, headers=native, json=body)).status_code == 403
        overwrite = {**body, "action": "overwrite"}
        response = await client.post(path, headers=native, json=overwrite)
        assert response.status_code == 200
        assert response.json() == {"text": generated}
        assert (
            await client.post(path, headers=native, json={**body, "action": "delete"})
        ).status_code == 422

        monkeypatch.setattr(models, "ainvoke_intranet_text", revoke_then_generate)
        response = await client.post(path, headers=native, json=overwrite)
        assert response.status_code == 403
        assert generated not in response.text


async def test_docx_generation_uses_only_text_and_returns_safe_failures(env, monkeypatch):
    from unittest.mock import AsyncMock

    from octop.infra.errors import ErrorCode, OctopError

    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    model = AsyncMock(return_value="整理后的中文正文")
    monkeypatch.setattr(
        server.app_runtime.agent_registry.host_models,
        "ainvoke_intranet_text",
        model,
    )
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/docx/generate"
        body = {"instruction": "整理会议记录", "text": "会议讨论下周计划。"}
        response = await client.post(path, headers=native, json=body)
        assert response.status_code == 200
        assert response.json() == {"text": "整理后的中文正文"}
        assert "会议讨论下周计划。" in model.call_args.args[1]
        assert "tools" not in model.call_args.kwargs
        for failure in (
            RuntimeError("secret-key-and-original-document"),
            OctopError(ErrorCode.INTERNAL_ERROR, "secret-key-and-original-document"),
        ):
            model.side_effect = failure
            response = await client.post(path, headers=native, json=body)
            assert response.status_code == 500
            assert response.json()["error"]["code"] == "LOCAL_DOCX_GENERATION_FAILED"
            assert "secret-key" not in response.text
            assert "original-document" not in response.text


@pytest.mark.parametrize("format", ["docx", "xlsx"])
async def test_local_task_rejects_another_employees_expert(env, format):
    client, server, admin = env
    alice = await create_user(
        client, admin, username="alice", permissions=[*GRANTS, "local_xlsx", "experts"]
    )
    bob = await create_user(client, admin, username="bob")
    bob_id = (await client.get("/api/auth/me", headers=bob)).json()["id"]
    server.services.agent_repo.create(
        agent_id="bobs-expert", user_id=bob_id, name="Private", default_model="openai/gpt-4o"
    )
    async with approved_device(client, admin, alice) as (device_id, native):
        response = await client.post(
            f"/api/devices/{device_id}/{format}/generate",
            headers=native,
            json={"agent_id": "bobs-expert", "instruction": "整理", "text": "资料"},
        )
        assert response.status_code == 403
        for action in ("read", "create"):
            response = await client.post(
                f"/api/devices/{device_id}/{format}/authorize",
                headers=native,
                json={"agent_id": "bobs-expert", "action": action},
            )
            assert response.status_code == 403


@pytest.mark.parametrize("format", ["docx", "xlsx"])
async def test_local_expert_uses_selected_model_and_discards_revoked_result(
    env, monkeypatch, format
):
    from unittest.mock import AsyncMock

    client, server, admin = env
    alice = await create_user(
        client, admin, username="alice", permissions=[*GRANTS, "local_xlsx", "experts"]
    )
    alice_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("unavailable", "do-not-fallback")
    server.services.agent_repo.create(
        agent_id="private-expert",
        user_id=alice_id,
        name="Private",
        default_model="openai/gpt-4o",
        system_prompt="列出待办负责人。",
        config_json=json.dumps({"local_only": True, "local_actions": [f"local_{format}"]}),
    )
    model = AsyncMock(return_value="整理后的结果")
    monkeypatch.setattr(
        server.app_runtime.agent_registry.host_models, "ainvoke_intranet_text", model
    )
    async with approved_device(client, admin, alice) as (device_id, native):
        body = {"agent_id": "private-expert", "instruction": "整理", "text": "资料"}
        path = f"/api/devices/{device_id}/{format}/generate"
        response = await client.post(path, headers=native, json=body)
        assert response.status_code == 200
        assert response.json() == {"text": "整理后的结果"}
        assert model.call_args.args[0] == "openai/gpt-4o"
        assert "列出待办负责人。" in model.call_args.kwargs["system"]

        async def change_configuration(*args, **kwargs):
            server.services.agent_repo.update_config(
                "private-expert", system_prompt="Changed while running"
            )
            return "stale configuration result"

        model.side_effect = change_configuration
        changed = await client.post(path, headers=native, json=body)
        assert changed.status_code == 403
        assert "stale configuration result" not in changed.text

        async def revoke(*args, **kwargs):
            server.services.agent_repo.update_config(
                "private-expert",
                config_json=json.dumps(
                    {
                        "local_only": True,
                        "local_actions": ["local_xlsx" if format == "docx" else "local_docx"],
                    }
                ),
            )
            return "不得返回的结果"

        model.side_effect = revoke
        response = await client.post(path, headers=native, json=body)
        assert response.status_code == 403
        assert "不得返回的结果" not in response.text
        response = await client.post(
            f"/api/devices/{device_id}/{format}/authorize",
            headers=native,
            json={"agent_id": "private-expert", "action": "create"},
        )
        assert response.status_code == 403


@pytest.mark.parametrize("missing", GRANTS)
async def test_docx_rechecks_changed_permissions_on_each_operation(env, missing):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    user_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    async with approved_device(client, admin, alice) as (device_id, native):
        changed = await client.patch(
            f"/api/users/{user_id}",
            headers=admin,
            json={"permissions": [key for key in GRANTS if key != missing]},
        )
        assert changed.status_code == 200
        action = "create" if missing == "local_files_create" else "read"
        response = await client.post(
            f"/api/devices/{device_id}/docx/authorize", headers=native, json={"action": action}
        )
        assert response.status_code == 403
        response = await client.post(
            f"/api/devices/{device_id}/docx/generate",
            headers=native,
            json={"instruction": "summarize", "text": "source"},
        )
        assert response.status_code == 403


async def test_docx_rejects_other_employee_admin_and_device_secrets(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    bob = await create_user(client, admin, username="bob", permissions=GRANTS)
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/docx/authorize"
        for other in (bob, admin):
            forged = {**native, **other}
            assert (
                await client.post(path, headers=forged, json={"action": "read"})
            ).status_code == 403
        async with approved_device(client, admin, alice) as (_other_id, other_native):
            assert (
                await client.post(path, headers=other_native, json={"action": "read"})
            ).status_code == 403
        await client.post(f"/api/devices/{device_id}/revoke", headers=admin)
        assert (await client.post(path, headers=native, json={"action": "read"})).status_code == 403
    async with approved_device(client, admin, admin) as (device_id, native):
        # Management role and device ownership do not imply file execution rights.
        assert (
            await client.post(
                f"/api/devices/{device_id}/docx/authorize",
                headers=native,
                json={"action": "create"},
            )
        ).status_code == 403


async def test_docx_discards_model_result_after_revocation(env, monkeypatch):

    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    async with approved_device(client, admin, alice) as (device_id, native):

        async def generate(*args, **kwargs):
            response = await client.post(f"/api/devices/{device_id}/revoke", headers=admin)
            assert response.status_code == 200
            return "must not be returned"

        monkeypatch.setattr(
            server.app_runtime.agent_registry.host_models,
            "ainvoke_intranet_text",
            generate,
        )
        response = await client.post(
            f"/api/devices/{device_id}/docx/generate",
            headers=native,
            json={"instruction": "summarize", "text": "private source"},
        )
        assert response.status_code == 403
        assert "must not be returned" not in response.text


async def test_docx_does_not_fallback_without_active_model(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    async with approved_device(client, admin, alice) as (device_id, native):
        response = await client.post(
            f"/api/devices/{device_id}/docx/generate",
            headers=native,
            json={"instruction": "summarize", "text": "private source"},
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "LOCAL_DOCX_GENERATION_FAILED"


async def test_docx_native_secret_rotates_on_reconnect(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    key = Ed25519PrivateKey.generate()
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        initial = await _hello(ws, alice, key)
        device_id = initial["device_id"]
        await client.post(f"/api/devices/{device_id}/approve", headers=admin)
        await ws.send_json({"type": "heartbeat"})
        old_secret = (await ws.receive_json())["session_token"]
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        current = await _hello(ws, alice, key)
        assert current["session_token"] != old_secret
        path = f"/api/devices/{device_id}/docx/authorize"
        for secret, status in ((old_secret, 403), (current["session_token"], 200)):
            response = await client.post(
                path, headers={**alice, "X-Colink-Device-Session": secret}, json={"action": "read"}
            )
            assert response.status_code == status


@pytest.mark.parametrize("output", ["", "x" * 60001], ids=["empty", "oversized"])
async def test_docx_rejects_unusable_model_output(env, monkeypatch, output):
    from unittest.mock import AsyncMock

    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    monkeypatch.setattr(
        server.app_runtime.agent_registry.host_models,
        "ainvoke_intranet_text",
        AsyncMock(return_value=output),
    )
    async with approved_device(client, admin, alice) as (device_id, native):
        response = await client.post(
            f"/api/devices/{device_id}/docx/generate",
            headers=native,
            json={"instruction": "summarize", "text": "source"},
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "LOCAL_DOCX_GENERATION_FAILED"


async def test_local_docx_permissions_are_explicit_in_public_catalog_and_auth(env):
    from octop.api.app import build_app

    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    for auth, expected in ((alice, set(GRANTS)), (admin, set())):
        profile = (await client.get("/api/auth/me", headers=auth)).json()
        assert set(profile["permissions"]) & set(GRANTS) == expected
    schema = build_app(server).openapi()
    for action in ("authorize", "generate"):
        operation = schema["paths"][f"/api/devices/{{device_id}}/docx/{action}"]["post"]
        assert operation["summary"]
        assert operation["description"]
        assert "$ref" in operation["responses"]["200"]["content"]["application/json"]["schema"]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://8.8.8.8/v1",
        "https://[::ffff:8.8.8.8]/v1",
        "https://model.example/v1",
        "https://user:password@10.0.0.1/v1",
        "ftp://10.0.0.1/v1",
    ],
)
async def test_docx_rejects_non_intranet_model_before_call(env, monkeypatch, base_url):
    import socket
    from unittest.mock import AsyncMock

    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    await seed_intranet_provider(client, admin, base_url)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    model = AsyncMock(return_value="must not call")
    monkeypatch.setattr(
        server.app_runtime.agent_registry.host_models, "ainvoke_intranet_text", model
    )
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443)),
        ],
    )
    async with approved_device(client, admin, alice) as (device_id, native):
        response = await client.post(
            f"/api/devices/{device_id}/docx/generate",
            headers=native,
            json={"instruction": "summarize", "text": "private source"},
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "LOCAL_DOCX_GENERATION_FAILED"
        model.assert_not_awaited()


@pytest.mark.parametrize("redirect", [False, True])
async def test_docx_real_model_client_is_pinned_and_does_not_follow_redirect(
    env, monkeypatch, redirect
):
    import json
    import socket

    import httpx

    import octop.infra.utils.ssrf_guard as network

    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    await seed_intranet_provider(client, admin, "http://enterprise-model.test/v1")
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.20.30.40", 80)),
        ],
    )
    requests = []

    def respond(request):
        requests.append(request)
        if redirect:
            return httpx.Response(
                307, headers={"location": "https://public.example/v1/chat/completions"}
            )
        body = json.loads(request.content)
        assert "tools" not in body
        assert "private source" in body["messages"][-1]["content"]
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "正文"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def pinned(host, pin_ip, *, strict_host):
        assert host == "enterprise-model.test"
        assert pin_ip == "10.20.30.40"
        assert strict_host is True
        return httpx.MockTransport(respond)

    monkeypatch.setattr(network, "PinnedIPTransport", pinned)
    async with approved_device(client, admin, alice) as (device_id, native):
        response = await client.post(
            f"/api/devices/{device_id}/docx/generate",
            headers=native,
            json={"instruction": "summarize", "text": "private source"},
        )
        assert response.status_code == (500 if redirect else 200)
        if not redirect:
            assert response.json() == {"text": "正文"}
        assert len(requests) == 1
        assert requests[0].url.host == "enterprise-model.test"


@pytest.mark.parametrize(
    "unsafe_model", ["different_endpoint", "api_fallback", "unsupported_protocol"]
)
async def test_docx_rejects_unsafe_factory_configuration_before_http(
    env, monkeypatch, unsafe_model
):
    from types import SimpleNamespace

    import httpx

    import octop.infra.utils.ssrf_guard as network

    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    factory = server.app_runtime.agent_registry.host_models
    model = factory.get("openai/gpt-4o").chat_model.model_copy()
    if unsafe_model == "different_endpoint":
        model.root_async_client = model.root_async_client.with_options(
            base_url="https://public.example/v1"
        )
    elif unsafe_model == "api_fallback":
        object.__setattr__(model, "_harness_api_fallback_model", object())
    else:
        model = object()
    monkeypatch.setattr(factory, "get", lambda ref: SimpleNamespace(chat_model=model))
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(500)

    monkeypatch.setattr(
        network, "PinnedIPTransport", lambda *args, **kwargs: httpx.MockTransport(respond)
    )
    async with approved_device(client, admin, alice) as (device_id, native):
        response = await client.post(
            f"/api/devices/{device_id}/docx/generate",
            headers=native,
            json={"instruction": "summarize", "text": "private source"},
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "LOCAL_DOCX_GENERATION_FAILED"
        assert requests == []
