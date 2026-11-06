"""Trusted employee device enrollment through the public HTTP/WS boundary."""

from __future__ import annotations

import asyncio
import base64
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tests.support.auth import create_user
from tests.support.http import ws_connect, ws_token


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgresql)])
async def env(request, tmp_octop_home):
    from tests.support.app import octop_client, write_octop_config
    from tests.support.auth import auth_header, bootstrap_admin

    if request.param == "postgresql":
        import psycopg

        dsn = os.environ.get("OCTOP_TEST_DATABASE_URL")
        if not dsn:
            pytest.skip("set OCTOP_TEST_DATABASE_URL to a dedicated PostgreSQL test database")
        # Same dedicated-database contract as the existing PostgreSQL integration suite.
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        write_octop_config(tmp_octop_home, database={"driver": "postgresql", "url": dsn})
    async with octop_client(tmp_octop_home) as (client, server):
        await bootstrap_admin(client, tmp_octop_home)
        yield client, server, await auth_header(client)


async def test_device_rejects_noncanonical_public_key(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice")
    key = Ed25519PrivateKey.generate()
    encoded = base64.b64encode(
        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    # The low two pad bits do not change the decoded key. They must not create a second identity.
    alternate = encoded[:-2] + alphabet[alphabet.index(encoded[-2]) + 1] + "="
    assert base64.b64decode(alternate) == base64.b64decode(encoded)
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        await ws.send_json(
            {
                "type": "hello",
                "access_token": ws_token(alice),
                "public_key": alternate,
                "name": "电脑",
                "platform": "linux-arm64",
            }
        )
        challenge = await ws.receive_json()
        await ws.send_json(
            {
                "type": "proof",
                "signature": base64.b64encode(key.sign(challenge["message"].encode())).decode(),
            }
        )
        assert (await ws.receive_json())["type"] == "error"
    assert (await client.get("/api/devices", headers=alice)).json() == []


async def _hello(ws, auth, key):
    public_key = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    await ws.send_json(
        {
            "type": "hello",
            "access_token": ws_token(auth),
            "public_key": base64.b64encode(public_key).decode(),
            "name": "员工电脑",
            "platform": "linux-arm64",
        }
    )
    challenge = await ws.receive_json()
    assert challenge["type"] == "challenge"
    await ws.send_json(
        {
            "type": "proof",
            "signature": base64.b64encode(key.sign(challenge["message"].encode())).decode(),
        }
    )
    return await ws.receive_json()


async def test_employee_enrolls_current_device_and_admin_approves(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice")
    listed = await client.get("/api/devices", headers=alice)
    assert listed.status_code == 200
    assert listed.json() == []
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        state = await _hello(ws, alice, Ed25519PrivateKey.generate())
        assert state["status"] == "pending"
        assert state["online"] is False
        device_id = state["device_id"]
        pending = (await client.get("/api/devices/manage", headers=admin)).json()
        assert pending[0]["device_id"] == device_id
        assert pending[0]["owner_username"] == "alice"
        approved = await client.post(f"/api/devices/{device_id}/approve", headers=admin)
        assert approved.status_code == 200
        await ws.send_json({"type": "heartbeat"})
        assert (await ws.receive_json())["status"] == "approved"
        own = (await client.get("/api/devices", headers=alice)).json()
        assert own[0]["online"] is True
    own = (await client.get("/api/devices", headers=alice)).json()
    assert own[0]["status"] == "approved"
    assert own[0]["online"] is False


@pytest.mark.parametrize("actor", ["employee", "administrator"])
async def test_other_account_cannot_take_over_device_even_with_its_key(env, actor):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice")
    attacker = (
        admin if actor == "administrator" else await create_user(client, admin, username="bob")
    )
    key = Ed25519PrivateKey.generate()
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        state = await _hello(ws, alice, key)
    await client.post(f"/api/devices/{state['device_id']}/approve", headers=admin)
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        rejected = await _hello(ws, attacker, key)
        assert rejected["type"] == "error"
        assert rejected["code"] == "FORBIDDEN"
    assert (await client.get("/api/devices", headers=attacker)).json() == []
    own = (await client.get("/api/devices", headers=alice)).json()
    assert len(own) == 1
    assert own[0]["online"] is False


@pytest.mark.parametrize("anonymous", [True, False])
async def test_device_trust_requires_administrator(env, anonymous):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice")
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        state = await _hello(ws, alice, Ed25519PrivateKey.generate())
    auth = {} if anonymous else alice
    expected = 401 if anonymous else 403
    assert (await client.get("/api/devices/manage", headers=auth)).status_code == expected
    for action in ("approve", "revoke"):
        assert (
            await client.post(f"/api/devices/{state['device_id']}/{action}", headers=auth)
        ).status_code == expected
    assert (await client.get("/api/devices", headers=alice)).json()[0]["status"] == "pending"
    if anonymous:
        assert (await client.get("/api/devices")).status_code == 401


async def test_device_requires_valid_proof_and_fresh_challenge(env):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice")
    key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(
        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        await ws.send_json(
            {
                "type": "hello",
                "access_token": ws_token(alice),
                "public_key": public_key,
                "name": "电脑",
                "platform": "linux-arm64",
            }
        )
        challenge = await ws.receive_json()
        old_signature = base64.b64encode(key.sign(challenge["message"].encode())).decode()
        wrong_signature = base64.b64encode(
            Ed25519PrivateKey.generate().sign(challenge["message"].encode())
        ).decode()
        await ws.send_json({"type": "proof", "signature": wrong_signature})
        assert (await ws.receive_json())["code"] == "AUTH_FAILED"
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        await ws.send_json(
            {
                "type": "hello",
                "access_token": ws_token(alice),
                "public_key": public_key,
                "name": "电脑",
                "platform": "linux-arm64",
            }
        )
        fresh = await ws.receive_json()
        assert fresh["message"] != challenge["message"]
        await ws.send_json({"type": "proof", "signature": old_signature})
        assert (await ws.receive_json())["code"] == "AUTH_FAILED"
    assert (await client.get("/api/devices", headers=alice)).json() == []


@pytest.mark.parametrize("action", ["revoke", "disable", "reset"])
async def test_live_device_loses_access_after_revocation_or_account_change(env, action):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice")
    alice_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    key = Ed25519PrivateKey.generate()
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        state = await _hello(ws, alice, key)
        device_id = state["device_id"]
        assert (
            await client.post(f"/api/devices/{device_id}/approve", headers=admin)
        ).status_code == 200
        if action == "revoke":
            changed = await client.post(f"/api/devices/{device_id}/revoke", headers=admin)
        elif action == "disable":
            changed = await client.patch(
                f"/api/users/{alice_id}", headers=admin, json={"disabled": True}
            )
        else:
            changed = await client.post(
                f"/api/users/{alice_id}/reset-password",
                headers=admin,
                json={"new_password": "NewPass123"},
            )
        assert changed.is_success
        managed = (await client.get("/api/devices/manage", headers=admin)).json()
        assert managed[0]["online"] is False
        await ws.send_json({"type": "heartbeat"})
        assert (await ws.receive_json())["type"] == "error"
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        if action == "revoke":
            assert (await _hello(ws, alice, key))["code"] == "FORBIDDEN"
        else:
            await ws.send_json(
                {
                    "type": "hello",
                    "access_token": ws_token(alice),
                    "public_key": base64.b64encode(
                        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
                    ).decode(),
                    "name": "电脑",
                    "platform": "linux-arm64",
                }
            )
            assert (await ws.receive_json())["type"] == "error"


async def test_device_trust_survives_server_restart_but_online_state_does_not(tmp_octop_home):
    from tests.support.app import octop_client
    from tests.support.auth import auth_header, bootstrap_admin

    key = Ed25519PrivateKey.generate()
    async with octop_client(tmp_octop_home) as (client, _server):
        await bootstrap_admin(client, tmp_octop_home)
        admin = await auth_header(client)
        alice = await create_user(client, admin, username="alice")
        async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
            state = await _hello(ws, alice, key)
            device_id = state["device_id"]
            await client.post(f"/api/devices/{device_id}/approve", headers=admin)
            assert (await client.get("/api/devices", headers=alice)).json()[0]["online"] is True
    async with octop_client(tmp_octop_home) as (client, _server):
        own = (await client.get("/api/devices", headers=alice)).json()
        assert own[0]["device_id"] == device_id
        assert own[0]["status"] == "approved"
        assert own[0]["online"] is False
        async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
            reconnected = await _hello(ws, alice, key)
            assert reconnected["device_id"] == device_id
            assert reconnected["online"] is True


async def test_binary_device_handshake_is_rejected(env):
    client, _server, admin = env
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        await ws.send_bytes(b"not a JSON text frame")
        assert (await ws.receive_json())["type"] == "error"
    assert (await client.get("/api/devices/manage", headers=admin)).json() == []


async def test_device_without_heartbeat_becomes_offline(env, monkeypatch):
    import octop.api.routers.devices as router
    import octop.infra.control.devices as domain

    monkeypatch.setattr(router, "DEVICE_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(domain, "DEVICE_TIMEOUT_SECONDS", 0.1)
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice")
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        state = await _hello(ws, alice, Ed25519PrivateKey.generate())
        await client.post(f"/api/devices/{state['device_id']}/approve", headers=admin)
        assert (await client.get("/api/devices", headers=alice)).json()[0]["online"] is True
        assert (await ws.receive_json())["type"] == "error"
        assert (await client.get("/api/devices", headers=alice)).json()[0]["online"] is False


async def test_device_accepts_http_renewal_after_original_token_expires(env):
    from octop.api.deps import ACCESS_TOKEN_RESPONSE_HEADER
    from octop.infra.auth.tokens import PURPOSE_ACCESS, sign_token
    from tests.support.auth import bearer

    client, server, admin = env
    alice = await create_user(client, admin, username="alice")
    user_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    short = bearer(
        sign_token(
            server.services.secret_repo.get("jwt"),
            sub=user_id,
            uname="alice",
            role="user",
            purpose=PURPOSE_ACCESS,
            ttl_seconds=3,
        )
    )
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        state = await _hello(ws, short, Ed25519PrivateKey.generate())
        await client.post(f"/api/devices/{state['device_id']}/approve", headers=admin)
        response = await client.get("/api/devices", headers=short)
        renewed = response.headers[ACCESS_TOKEN_RESPONSE_HEADER]
        await asyncio.sleep(3.1)
        assert (await client.get("/api/devices", headers=bearer(renewed))).status_code == 200
        await ws.send_json({"type": "heartbeat", "access_token": renewed})
        refreshed = await ws.receive_json()
        assert refreshed["type"] == "status"
        assert refreshed["device_id"] == state["device_id"]
        assert refreshed["online"] is True
        # A legacy heartbeat continues using the most recently authenticated token.
        await ws.send_json({"type": "heartbeat"})
        assert (await ws.receive_json())["online"] is True
        await ws.send_json({"type": "heartbeat", "access_token": ws_token(short)})
        assert (await ws.receive_json())["code"] == "TOKEN_EXPIRED"


@pytest.mark.parametrize("actor", ["employee", "administrator"])
async def test_device_token_refresh_cannot_switch_owner(env, actor):
    client, _server, admin = env
    alice = await create_user(client, admin, username="alice")
    other = admin if actor == "administrator" else await create_user(client, admin, username="bob")
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        state = await _hello(ws, alice, Ed25519PrivateKey.generate())
        await client.post(f"/api/devices/{state['device_id']}/approve", headers=admin)
        await ws.send_json({"type": "heartbeat", "access_token": ws_token(other)})
        rejected = await ws.receive_json()
        assert rejected["code"] == "FORBIDDEN"
    assert (await client.get("/api/devices", headers=alice)).json()[0]["online"] is False


@pytest.mark.parametrize("action", ["revoke", "disable", "reset"])
async def test_device_refresh_cannot_restore_revoked_session(env, action):
    from tests.support.auth import auth_header

    client, _server, admin = env
    alice = await create_user(client, admin, username="alice")
    user_id = (await client.get("/api/auth/me", headers=alice)).json()["id"]
    async with ws_connect(client._octop_app, "/api/devices/connect") as ws:
        state = await _hello(ws, alice, Ed25519PrivateKey.generate())
        await client.post(f"/api/devices/{state['device_id']}/approve", headers=admin)
        if action == "reset":
            response = await client.post(
                f"/api/users/{user_id}/reset-password",
                headers=admin,
                json={"new_password": "NewPass123"},
            )
            assert response.status_code == 204
            alice = await auth_header(client, username="alice", password="NewPass123")
        elif action == "disable":
            response = await client.patch(
                f"/api/users/{user_id}", headers=admin, json={"disabled": True}
            )
            assert response.status_code == 200
        else:
            response = await client.post(f"/api/devices/{state['device_id']}/revoke", headers=admin)
            assert response.status_code == 200
        await ws.send_json({"type": "heartbeat", "access_token": ws_token(alice)})
        assert (await ws.receive_json())["type"] == "error"
    assert (await client.get("/api/devices/manage", headers=admin)).json()[0]["online"] is False
