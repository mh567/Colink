"""Native file modifications leave a 90-day audit trail without private content."""

from __future__ import annotations

import json

from tests.integration import test_devices
from tests.integration.test_local_docx import approved_device
from tests.support.auth import create_user

REQUEST_ID = "0123456789abcdef0123456789abcdef"
GRANTS = ["local_docx", "local_files_read", "local_files_create", "local_files_overwrite"]
env = test_devices.env


def _payloads(rows: list[dict]) -> list[dict]:
    return [json.loads(row["payload"]) for row in rows if row.get("payload")]


async def test_overwrite_trail_is_queryable_without_private_content(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/docx/authorize"
        allowed = await client.post(
            path,
            headers=native,
            json={
                "action": "overwrite",
                "request_id": REQUEST_ID,
                "path": "/员工/私有/合同.docx",
            },
        )
        assert allowed.status_code == 200
        for event, extra in (
            ("confirmed", {}),
            ("completed", {}),
        ):
            reported = await client.post(
                f"/api/devices/{device_id}/file-events",
                headers=native,
                json={
                    "request_id": REQUEST_ID,
                    "event": event,
                    "format": "docx",
                    "operation": "overwrite",
                    "path": "/员工/私有/合同.docx",
                    "instruction": "把身份证号抄下来",
                    "text": "完整原文",
                    "password": "hunter2",
                    **extra,
                },
            )
            assert reported.status_code == 200, reported.text

        admin_rows = (
            await client.get("/api/admin/audit-log?action=file.authorized", headers=admin)
        ).json()
        trail = [
            row
            for row in (await client.get("/api/admin/audit-log?limit=50", headers=admin)).json()
            if row["target"] == REQUEST_ID
        ]
        assert [row["action"] for row in reversed(trail)] == [
            "file.authorized",
            "file.confirmed",
            "file.completed",
        ]
        assert all(row["actor"] == "alice" for row in trail)
        dumped = json.dumps(_payloads(trail), ensure_ascii=False)
        assert "合同.docx" in dumped
        assert device_id in dumped
        assert "hunter2" not in dumped
        assert "身份证" not in dumped
        assert "完整原文" not in dumped
        assert "/员工/私有" not in dumped
        assert admin_rows
        assert (await client.get("/api/admin/audit-log", headers=alice)).status_code == 403


async def test_file_audit_records_denial_rejection_and_failure(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    async with approved_device(client, admin, alice) as (device_id, native):
        denied_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        denied = await client.post(
            f"/api/devices/{device_id}/docx/authorize",
            headers=native,
            json={"action": "delete", "request_id": denied_id, "path": "台账.xlsx"},
        )
        assert denied.status_code == 403

        rejected_id = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        assert (
            await client.post(
                f"/api/devices/{device_id}/docx/authorize",
                headers=native,
                json={"action": "overwrite", "request_id": rejected_id, "path": "纪要.docx"},
            )
        ).status_code == 200
        assert (
            await client.post(
                f"/api/devices/{device_id}/file-events",
                headers=native,
                json={
                    "request_id": rejected_id,
                    "event": "rejected",
                    "format": "docx",
                    "operation": "overwrite",
                    "path": "纪要.docx",
                },
            )
        ).status_code == 200

        failed_id = "cccccccccccccccccccccccccccccccc"
        assert (
            await client.post(
                f"/api/devices/{device_id}/file-events",
                headers=native,
                json={
                    "request_id": failed_id,
                    "event": "failed",
                    "format": "docx",
                    "operation": "overwrite",
                    "path": "纪要.docx",
                    "error_code": "target_changed",
                },
            )
        ).status_code == 200

        rows = (await client.get("/api/admin/audit-log?limit=50", headers=admin)).json()
        by_target = {row["target"]: row["action"] for row in rows}
        assert by_target[denied_id] == "file.authorization_denied"
        assert by_target[rejected_id] in {"file.rejected", "file.authorized"}
        assert any(
            row["target"] == rejected_id and row["action"] == "file.rejected" for row in rows
        )
        assert by_target[failed_id] == "file.failed"
        assert "target_changed" in (rows[0]["payload"] or "")
