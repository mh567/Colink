"""Provider credentials stay server-side, including draft probes."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


async def test_provider_credentials_never_returned(env: Any) -> None:
    client, _, auth = env
    response = await client.post(
        "/api/admin/providers",
        headers=auth,
        json={
            "name": "internal",
            "kind": "openai",
            "api_key": "enterprise-secret",
            "base_url": "http://models.internal/v1",
        },
    )
    assert response.status_code == 201
    provider_id = response.json()["id"]
    assert "enterprise-secret" not in response.text
    assert response.json()["api_key_set"] is True
    for path in ("/api/providers", "/api/admin/providers"):
        response = await client.get(path, headers=auth)
        assert "enterprise-secret" not in response.text
        assert "api_key" not in response.json()[0]
    response = await client.patch(
        f"/api/admin/providers/{provider_id}", headers=auth, json={"note": "updated"}
    )
    assert "enterprise-secret" not in response.text
    assert response.json()["api_key_set"] is True


@pytest.mark.parametrize("path", ["test-draft", "fetch-models"])
async def test_saved_credentials_cannot_follow_draft_destination(env: Any, path: str) -> None:
    client, _, auth = env
    created = await client.post(
        "/api/admin/providers",
        headers=auth,
        json={
            "name": "internal",
            "kind": "openai",
            "api_key": "enterprise-secret",
            "base_url": "http://models.internal/v1",
        },
    )
    body = {
        "provider_id": created.json()["id"],
        "name": "internal",
        "kind": "openai",
        "model_id": "chat",
        "base_url": "https://other.example/v1",
    }
    response = await client.post(f"/api/admin/providers/{path}", headers=auth, json=body)
    assert response.status_code == 400
    assert "enterprise-secret" not in response.text


async def test_saved_credentials_used_without_returning_them(env: Any) -> None:
    client, _, auth = env
    created = await client.post(
        "/api/admin/providers",
        headers=auth,
        json={
            "name": "internal",
            "kind": "openai",
            "api_key": "enterprise-secret",
            "base_url": "http://models.internal/v1",
        },
    )
    with patch(
        "octop.api.routers.providers.probe_provider_row", new=AsyncMock(return_value={"ok": True})
    ) as probe:
        response = await client.post(
            "/api/admin/providers/test-draft",
            headers=auth,
            json={
                "provider_id": created.json()["id"],
                "name": "internal",
                "kind": "openai",
                "model_id": "chat",
                "base_url": "http://models.internal/v1",
            },
        )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert probe.await_args.args[0].api_key == "enterprise-secret"


async def test_employee_cannot_read_or_test_credentials(env: Any) -> None:
    client, _, auth = env
    created = await client.post(
        "/api/admin/providers",
        headers=auth,
        json={
            "name": "internal",
            "kind": "openai",
            "api_key": "enterprise-secret",
            "base_url": "http://models.internal/v1",
        },
    )
    await client.post(
        "/api/users",
        headers=auth,
        json={"username": "employee", "password": "TestPass12", "role": "user"},
    )
    login = await client.post(
        "/api/auth/login", json={"username": "employee", "password": "TestPass12"}
    )
    employee = {"Authorization": "Bearer " + login.json()["access_token"]}
    response = await client.get("/api/providers", headers=employee)
    assert response.status_code == 200
    assert "enterprise-secret" not in response.text
    assert "api_key" not in response.json()[0]
    response = await client.post(
        f"/api/admin/providers/{created.json()['id']}/test", headers=employee
    )
    assert response.status_code == 403


async def test_saved_probe_status_is_separate_and_invalidated(env: Any) -> None:
    client, _, auth = env
    created = await client.post(
        "/api/admin/providers",
        headers=auth,
        json={
            "name": "internal",
            "kind": "openai",
            "api_key": "enterprise-secret",
            "base_url": "http://models.internal/v1",
            "models": [{"id": "chat"}],
        },
    )
    provider_id = created.json()["id"]
    with patch(
        "octop.api.routers.providers.probe_provider_row", new=AsyncMock(return_value={"ok": True})
    ):
        response = await client.post(
            f"/api/admin/providers/{provider_id}/test",
            headers=auth,
            json={"model_id": "chat", "capability": "stream"},
        )
    assert response.status_code == 200
    listed = await client.get("/api/admin/providers", headers=auth)
    status = listed.json()[0]["probe_results"]["chat"]
    assert set(status) == {"stream"}
    assert status["stream"]["ok"] is True
    assert status["stream"]["tested_at"] > 0
    changed = await client.patch(
        f"/api/admin/providers/{provider_id}",
        headers=auth,
        json={"base_url": "http://new.internal/v1"},
    )
    assert changed.json()["probe_results"] == {}


async def test_concurrent_capability_results_are_both_preserved(env: Any) -> None:
    import asyncio

    client, _, auth = env
    created = await client.post(
        "/api/admin/providers",
        headers=auth,
        json={
            "name": "internal",
            "kind": "openai",
            "api_key": "secret",
            "base_url": "http://models.internal/v1",
            "models": [{"id": "chat"}],
        },
    )
    provider_id = created.json()["id"]
    stream_started = asyncio.Event()
    finish_stream = asyncio.Event()

    async def probe(_row: Any, **kwargs: Any) -> dict[str, Any]:
        if kwargs["capability"] == "stream":
            stream_started.set()
            await finish_stream.wait()
        return {"ok": True, "capability": kwargs["capability"]}

    with patch("octop.api.routers.providers.probe_provider_row", side_effect=probe):
        stream = asyncio.create_task(
            client.post(
                f"/api/admin/providers/{provider_id}/test",
                headers=auth,
                json={"model_id": "chat", "capability": "stream"},
            )
        )
        await asyncio.wait_for(stream_started.wait(), 5)
        chat = await client.post(
            f"/api/admin/providers/{provider_id}/test", headers=auth, json={"model_id": "chat"}
        )
        finish_stream.set()
        assert (await stream).status_code == chat.status_code == 200
    listed = await client.get("/api/admin/providers", headers=auth)
    assert set(listed.json()[0]["probe_results"]["chat"]) == {"chat", "stream"}


@pytest.mark.parametrize(
    "override",
    [{"api_mode": "responses"}, {"api_fallback": "chat_completions"}, {"embedding": True}],
)
async def test_unsaved_probe_options_do_not_become_saved_status(
    env: Any, override: dict[str, Any]
) -> None:
    client, _, auth = env
    created = await client.post(
        "/api/admin/providers",
        headers=auth,
        json={
            "name": "internal",
            "kind": "openai",
            "api_key": "secret",
            "base_url": "http://models.internal/v1",
            "models": [{"id": "chat"}],
        },
    )
    with patch(
        "octop.api.routers.providers.probe_provider_row", new=AsyncMock(return_value={"ok": True})
    ):
        response = await client.post(
            f"/api/admin/providers/{created.json()['id']}/test",
            headers=auth,
            json={"model_id": "chat", **override},
        )
    assert response.json()["ok"] is True
    listed = await client.get("/api/admin/providers", headers=auth)
    assert listed.json()[0]["probe_results"] == {}


async def test_probe_openapi_describes_capability_results(env: Any) -> None:
    from octop.api.app import build_app

    _, server, _ = env
    schema = build_app(server).openapi()
    for path in ("/api/admin/providers/test-draft", "/api/admin/providers/{provider_id}/test"):
        result = schema["paths"][path]["post"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        assert result["$ref"] == "#/components/schemas/ProviderProbeResult"
    properties = schema["components"]["schemas"]["ProviderProbeResult"]["properties"]
    assert {"ok", "error_type", "dimension", "capability", "latency_ms"} <= properties.keys()


@pytest.mark.parametrize(
    ("body", "enabled", "expected"),
    [(None, True, "second"), ({"model_id": "first"}, True, "first"), (None, False, "first")],
)
async def test_onnx_probe_records_actual_model(
    env: Any, body: dict[str, str] | None, enabled: bool, expected: str
) -> None:
    client, _, auth = env
    created = await client.post(
        "/api/admin/providers",
        headers=auth,
        json={
            "name": "onnx",
            "kind": "openai",
            "api_key": "onnx",
            "models": [
                {"id": "first", "enabled": False, "embedding": True},
                {"id": "second", "enabled": enabled, "embedding": True},
            ],
        },
    )
    assert created.status_code == 201
    with patch(
        "octop.infra.agents.providers.onnx_service.probe_local_model",
        new=AsyncMock(return_value={"ok": True, "dim": 384}),
    ) as probe:
        response = await client.post(
            f"/api/admin/providers/{created.json()['id']}/test", headers=auth, json=body
        )
    assert response.status_code == 200
    probe.assert_awaited_once_with(expected)
    listed = await client.get("/api/admin/providers", headers=auth)
    results = listed.json()[0]["probe_results"]
    assert set(results) == {expected}
    assert results[expected]["embedding"]["ok"] is True
    assert results[expected]["embedding"]["dim"] == 384
