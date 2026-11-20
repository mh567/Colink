"""In-process API coverage for T22; no Docker, model calls, or desktop client."""

from __future__ import annotations

import platform
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from octop import __version__
from tests.integration.test_devices import _hello
from tests.integration.test_local_docx import seed_intranet_provider
from tests.support.app import octop_client
from tests.support.auth import TEST_PASSWORD, auth_header, bootstrap_admin, create_user
from tests.support.http import ws_connect

pytestmark = pytest.mark.asyncio

LOCAL_GRANTS = [
    "local_docx",
    "local_xlsx",
    "local_browser",
    "local_files_read",
    "local_files_create",
]


async def test_offline_api_bootstrap_and_restart_persistence(tmp_octop_home: Path) -> None:
    key = Ed25519PrivateKey.generate()
    request_id = "0123456789abcdef0123456789abcdef"

    async with octop_client(tmp_octop_home, bind_database=False, seed_default_expert=False) as (
        client,
        server,
    ):
        # An empty data directory is reachable before the wizard binds its database.
        health_resp = await client.get("/api/health")
        assert health_resp.status_code == 200
        health_data = health_resp.json()
        assert health_data["ok"] is True
        assert health_data["version"] == __version__
        assert health_data["arch"] == platform.machine()
        assert health_data["db"] is False

        bound = await client.post(
            "/api/setup/database", json={"driver": "sqlite", "sqlite_path": "octop.db"}
        )
        assert bound.status_code == 200, bound.text
        await bootstrap_admin(client, tmp_octop_home)
        admin_auth = await auth_header(client)
        initial_agents = await client.get("/api/agents", headers=admin_auth)
        assert initial_agents.status_code == 200, initial_agents.text
        assert initial_agents.json() == []

        # 3. Configure Intranet Model Provider (no public internet access)
        await seed_intranet_provider(client, admin_auth, base_url="http://10.20.30.40/v1")
        providers = (await client.get("/api/admin/providers", headers=admin_auth)).json()
        assert any(p["base_url"] == "http://10.20.30.40/v1" for p in providers)

        expert = await client.post(
            "/api/agents/from-expert/default", headers=admin_auth, json={"name": "Intranet Expert"}
        )
        assert expert.status_code == 201, expert.text
        agents = (await client.get("/api/agents", headers=admin_auth)).json()
        assert any(
            a["name"] == "Intranet Expert" and a["template_name"] == "default" for a in agents
        )

        # 4. Create employee account with local permissions
        employee_auth = await create_user(
            client, admin_auth, username="emp_offline", permissions=LOCAL_GRANTS
        )

        # 5. Connect employee device and perform admin approval
        async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
            hello_state = await _hello(ws, employee_auth, key)
            assert hello_state["status"] == "pending"
            assert hello_state["online"] is False
            device_id = hello_state["device_id"]

            # Admin approves device
            approve_resp = await client.post(
                f"/api/devices/{device_id}/approve", headers=admin_auth
            )
            assert approve_resp.status_code == 200

            # Device receives approved status and session_token on heartbeat
            await ws.send_json({"type": "heartbeat"})
            hb_state = await ws.receive_json()
            assert hb_state["status"] == "approved"
            session_token = hb_state["session_token"]

            dev_headers = {
                **employee_auth,
                "X-Colink-Device-Session": session_token,
            }

            # The test client requests authorization and submits a simulated completion event.
            # DOCX authorize
            docx_authz = await client.post(
                f"/api/devices/{device_id}/docx/authorize",
                headers=dev_headers,
                json={
                    "action": "create",
                    "request_id": request_id,
                    "path": "report.docx",
                },
            )
            assert docx_authz.status_code == 200
            assert docx_authz.json().get("allowed") is True

            # Record file audit event
            event_resp = await client.post(
                f"/api/devices/{device_id}/file-events",
                headers=dev_headers,
                json={
                    "request_id": request_id,
                    "event": "completed",
                    "format": "docx",
                    "operation": "create",
                    "path": "report.docx",
                },
            )
            assert event_resp.status_code == 200
            assert event_resp.json().get("recorded") is True

            # XLSX authorize
            xlsx_authz = await client.post(
                f"/api/devices/{device_id}/xlsx/authorize",
                headers=dev_headers,
                json={"action": "create", "path": "data.xlsx"},
            )
            assert xlsx_authz.status_code == 200
            assert xlsx_authz.json().get("allowed") is True

            # Local browser authorize
            browser_authz = await client.post(
                f"/api/devices/{device_id}/browser/authorize",
                headers=dev_headers,
                json={"action": "open"},
            )
            assert browser_authz.status_code == 200
            assert browser_authz.json().get("allowed") is True

            # 7. Invariants:
            # - Negative: missing device session is rejected
            no_session = await client.post(
                f"/api/devices/{device_id}/docx/authorize",
                headers=employee_auth,
                json={"action": "create", "path": "report.docx"},
            )
            assert no_session.status_code in (401, 403)

            # - Negative: action="execute" (arbitrary execution) is rejected
            exec_attempt = await client.post(
                f"/api/devices/{device_id}/docx/authorize",
                headers=dev_headers,
                json={"action": "execute", "path": "report.docx"},
            )
            assert exec_attempt.status_code == 422

            # - Invariant: no external IM channels running in gateway
            assert len(server.app_runtime.gateway._held_channels) == 0

    async with octop_client(tmp_octop_home, seed_default_expert=False) as (client2, server2):
        # 1. Health check passes after restart
        health2 = await client2.get("/api/health")
        assert health2.status_code == 200
        assert health2.json()["ok"] is True
        assert health2.json()["version"] == __version__
        assert health2.json()["arch"] == platform.machine()
        assert health2.json()["db"] is True

        admin_auth2 = await auth_header(client2)

        # 3. Employee login succeeds with existing credentials
        login_resp = await client2.post(
            "/api/auth/login",
            json={"username": "emp_offline", "password": TEST_PASSWORD},
        )
        assert login_resp.status_code == 200
        emp_token2 = login_resp.json()["access_token"]
        employee_auth2 = {"Authorization": f"Bearer {emp_token2}"}

        # 4. Intranet model provider persisted across restart
        providers2 = (await client2.get("/api/admin/providers", headers=admin_auth2)).json()
        assert any(p["base_url"] == "http://10.20.30.40/v1" for p in providers2)
        audit = await client2.get(
            "/api/admin/audit-log", headers=admin_auth2, params={"request_id": request_id}
        )
        assert audit.status_code == 200, audit.text
        assert any(
            row["action"] == "file.completed" and row["actor"] == "emp_offline"
            for row in audit.json()
        )

        # 5. Device trust persisted across restart (status remains approved)
        managed_devices = (await client2.get("/api/devices/manage", headers=admin_auth2)).json()
        approved_dev = next((d for d in managed_devices if d["device_id"] == device_id), None)
        assert approved_dev is not None
        assert approved_dev["status"] == "approved"
        assert approved_dev["owner_username"] == "emp_offline"

        # 6. Reconnected device retains approved status without requiring re-approval
        async with ws_connect(client2._octop_app, "/api/devices/connect") as ws2:
            reconnect_state = await _hello(ws2, employee_auth2, key)
            assert reconnect_state["status"] == "approved"
            assert "session_token" in reconnect_state

        # 7. No external IM channels running after restart
        assert len(server2.app_runtime.gateway._held_channels) == 0
