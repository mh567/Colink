"""Local OCR authorization never accepts document content or implicit admin rights."""

from __future__ import annotations

import pytest

from tests.integration import test_devices
from tests.integration.test_local_docx import approved_device
from tests.support.auth import create_user

env = test_devices.env
GRANTS = ["local_ocr", "local_files_read", "local_files_create"]


async def test_ocr_requires_feature_and_native_device_possession(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    bob = await create_user(client, admin, username="bob", permissions=GRANTS)
    async with approved_device(client, admin, alice) as (device_id, native):
        path = f"/api/devices/{device_id}/ocr/authorize"
        for action in ("read", "create"):
            response = await client.post(path, headers=native, json={"action": action})
            assert response.status_code == 200
            assert response.json() == {"allowed": True}
        for headers in (alice, admin, {**native, **bob}):
            assert (
                await client.post(path, headers=headers, json={"action": "read"})
            ).status_code == 403
        async with approved_device(client, admin, alice) as (_other_id, other_native):
            assert (
                await client.post(path, headers=other_native, json={"action": "read"})
            ).status_code == 403
        assert (
            await client.post(path, headers=native, json={"action": "execute"})
        ).status_code == 422
        assert (
            await client.post(path, headers=native, json={"action": "read", "text": "private"})
        ).status_code == 422
    async with approved_device(client, admin, admin) as (device_id, native):
        assert (
            await client.post(
                f"/api/devices/{device_id}/ocr/authorize", headers=native, json={"action": "read"}
            )
        ).status_code == 403


@pytest.mark.parametrize(
    "missing,action",
    [("local_ocr", "read"), ("local_files_read", "read"), ("local_files_create", "create")],
)
async def test_ocr_rechecks_revoked_permissions(env, missing, action):
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
        assert (
            await client.post(
                f"/api/devices/{device_id}/ocr/authorize", headers=native, json={"action": action}
            )
        ).status_code == 403


async def test_ocr_rejects_revoked_device_and_has_no_upload_endpoint(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice", permissions=GRANTS)
    async with approved_device(client, admin, alice) as (device_id, native):
        assert (
            await client.post(
                f"/api/devices/{device_id}/ocr/generate", headers=native, json={"text": "private"}
            )
        ).status_code in {404, 405}  # A built dashboard mounts a GET-only SPA fallback.
        assert (
            await client.post(f"/api/devices/{device_id}/revoke", headers=admin)
        ).status_code == 200
        assert (
            await client.post(
                f"/api/devices/{device_id}/ocr/authorize", headers=native, json={"action": "read"}
            )
        ).status_code == 403
