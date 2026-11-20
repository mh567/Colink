"""Integration tests for revoking authorization or disabling accounts during execution.

Covers Issue #16 ([T15] 运行中停用账号与撤销授权) and user stories 18, 49, 56:
- Disabling an employee account between task steps stops further controlled operations.
- Revoking device authorization between task steps stops further controlled operations.
- Disabling account or revoking device during generation discards the model result.
- Administrators cannot bypass device boundaries or execute without explicit grants.
- Service disconnection prevents further controlled operations.
- Completed business actions are not claimed to be undone.
"""

from __future__ import annotations

import pytest

from octop.infra.errors import ErrorCode, OctopError
from tests.integration import test_devices
from tests.integration.test_local_docx import approved_device, seed_intranet_provider
from tests.support.auth import create_user

DOCX_GRANTS = ["local_docx", "local_files_read", "local_files_create"]
BROWSER_GRANTS = ["local_browser"]
env = test_devices.env


async def test_account_disabled_between_steps_rejects_subsequent_tool_calls(env):
    """Between task steps, disabling the account immediately rejects subsequent tool calls."""
    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=DOCX_GRANTS)
    alice_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]

    async with approved_device(client, admin, alice) as (device_id, native):
        # Step 1: Read authorization succeeds
        read_resp = await client.post(
            f"/api/devices/{device_id}/docx/authorize",
            headers=native,
            json={"action": "read", "request_id": "0" * 32, "path": "source.docx"},
        )
        assert read_resp.status_code == 200
        assert read_resp.json() == {"allowed": True}

        # Admin disables the employee account between steps
        disable_resp = await client.patch(
            f"/api/users/{alice_id}", headers=admin, json={"disabled": True}
        )
        assert disable_resp.status_code == 200

        # Step 2: Subsequent create authorization must be rejected
        create_resp = await client.post(
            f"/api/devices/{device_id}/docx/authorize",
            headers=native,
            json={"action": "create", "request_id": "0" * 32, "path": "output.docx"},
        )
        assert create_resp.status_code == 403

        # Step 3: Generation attempt must also be rejected
        gen_resp = await client.post(
            f"/api/devices/{device_id}/docx/generate",
            headers=native,
            json={"instruction": "summarize", "text": "extracted source text"},
        )
        assert gen_resp.status_code == 403

        # Device is no longer considered online
        managed = (await client.get("/api/devices/manage", headers=admin)).json()
        assert managed[0]["online"] is False

        # Verification: create authorization was denied
        assert create_resp.status_code == 403
        assert create_resp.json()["error"]["code"] in {"USER_DISABLED", "FORBIDDEN"}


async def test_device_revoked_between_steps_rejects_subsequent_tool_calls(env):
    """Between task steps, revoking device trust immediately rejects subsequent tool calls."""
    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=DOCX_GRANTS)

    async with approved_device(client, admin, alice) as (device_id, native):
        # Step 1: Read authorization succeeds
        read_resp = await client.post(
            f"/api/devices/{device_id}/docx/authorize",
            headers=native,
            json={"action": "read", "request_id": "1" * 32, "path": "source.docx"},
        )
        assert read_resp.status_code == 200

        # Admin revokes the device trust between steps
        revoke_resp = await client.post(f"/api/devices/{device_id}/revoke", headers=admin)
        assert revoke_resp.status_code == 200
        assert revoke_resp.json()["status"] == "revoked"

        # Step 2: Subsequent create authorization must be rejected
        create_resp = await client.post(
            f"/api/devices/{device_id}/docx/authorize",
            headers=native,
            json={"action": "create", "request_id": "1" * 32, "path": "output.docx"},
        )
        assert create_resp.status_code == 403

        # Step 3: Generation attempt must be rejected
        gen_resp = await client.post(
            f"/api/devices/{device_id}/docx/generate",
            headers=native,
            json={"instruction": "summarize", "text": "source"},
        )
        assert gen_resp.status_code == 403


@pytest.mark.parametrize("revocation", ["disable_account", "revoke_device"])
async def test_docx_generation_discards_model_output_on_revocation(env, monkeypatch, revocation):
    """Disabling account or revoking device during generation discards the model result."""
    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=DOCX_GRANTS)
    alice_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")

    async with approved_device(client, admin, alice) as (device_id, native):

        async def revoke_during_call(*args, **kwargs):
            if revocation == "disable_account":
                resp = await client.patch(
                    f"/api/users/{alice_id}", headers=admin, json={"disabled": True}
                )
            else:
                resp = await client.post(f"/api/devices/{device_id}/revoke", headers=admin)
            assert resp.status_code == 200
            return "confidential generated output that must never be returned"

        monkeypatch.setattr(
            server.app_runtime.agent_registry.host_models,
            "ainvoke_intranet_text",
            revoke_during_call,
        )

        response = await client.post(
            f"/api/devices/{device_id}/docx/generate",
            headers=native,
            json={"instruction": "summarize", "text": "private source"},
        )
        assert response.status_code == 403
        assert "confidential generated output" not in response.text


@pytest.mark.parametrize("revocation", ["disable_account", "revoke_device"])
async def test_browser_query_discards_result_on_revocation(env, monkeypatch, revocation):
    """Browser queries discard model results if the account is disabled or device revoked."""
    client, server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=BROWSER_GRANTS)
    alice_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    await seed_intranet_provider(client, admin)
    server.services.settings_repo.set_active_model("openai", "gpt-4o")
    answer = "企业库存为 99 件。"

    async with approved_device(client, admin, alice) as (device_id, native):

        async def revoke_during_call(*args, **kwargs):
            if revocation == "disable_account":
                resp = await client.patch(
                    f"/api/users/{alice_id}", headers=admin, json={"disabled": True}
                )
            else:
                resp = await client.post(f"/api/devices/{device_id}/revoke", headers=admin)
            assert resp.status_code == 200
            return answer

        monkeypatch.setattr(
            server.app_runtime.agent_registry.host_models,
            "ainvoke_intranet_text",
            revoke_during_call,
        )

        path = f"/api/devices/{device_id}/browser/query"
        body = {"instruction": "查询库存", "text": "当前库存：99"}
        response = await client.post(path, headers=native, json=body)
        assert response.status_code == 403
        assert answer not in response.text


async def test_admin_cannot_bypass_device_boundaries(env):
    """Administrators cannot bypass device boundaries or execute without explicit grants."""
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=DOCX_GRANTS)

    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/docx/authorize"

        # Admin using admin token directly on employee's device
        assert (await client.post(path, headers=admin, json={"action": "read"})).status_code == 403

        # Admin forging native session token with admin auth
        forged = {**native, **admin}
        assert (await client.post(path, headers=forged, json={"action": "read"})).status_code == 403

    # Admin's own approved device without explicit file grants
    async with approved_device(client, admin, admin) as (admin_dev_id, admin_native):
        admin_path = f"/api/devices/{admin_dev_id}/docx/authorize"
        assert (
            await client.post(admin_path, headers=admin_native, json={"action": "create"})
        ).status_code == 403


async def test_service_disconnect_halts_running_operations(env):
    """Disconnected devices cannot execute new controlled operations."""
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=DOCX_GRANTS)

    # Acquire approved device session, then exit the context (disconnecting the WebSocket)
    async with approved_device(client, admin, alice) as (device_id, native):
        pass

    # Once disconnected, operations must be rejected immediately
    resp = await client.post(
        f"/api/devices/{device_id}/docx/authorize",
        headers=native,
        json={"action": "read"},
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("batch", [False, True])
async def test_failed_account_deletion_keeps_device_connected(env, monkeypatch, batch):
    client, server, admin = env
    employee = await create_user(client, admin, username="alice", permissions=DOCX_GRANTS)
    employee_id = (await client.get("/api/auth/me", headers=employee)).json()["id"]

    def reject_deletion(_owned_agents):
        raise OctopError.localized(ErrorCode.AGENT_BUSY)

    monkeypatch.setattr(
        server.app_runtime.agent_registry, "assert_agents_deletable", reject_deletion
    )
    async with approved_device(client, admin, employee) as (device_id, native):
        if batch:
            response = await client.post(
                "/api/users/batch",
                headers=admin,
                json={"action": "delete", "user_ids": [employee_id]},
            )
            assert response.json()["results"][0]["code"] == "AGENT_BUSY"
        else:
            response = await client.delete(f"/api/users/{employee_id}", headers=admin)
            assert response.status_code == 409
        response = await client.post(
            f"/api/devices/{device_id}/docx/authorize", headers=native, json={"action": "read"}
        )
        assert response.json() == {"allowed": True}
