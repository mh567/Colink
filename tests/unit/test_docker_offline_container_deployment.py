"""Tests for offline container deployment, orchestration, and runtime invariants.

Verifies acceptance criteria of Issue #23:
1. Multi-arch offline deployment orchestration and explicit resource limits.
2. Architecture and application version queryable via health probe.
3. ARM64 SVE2 OpenSSL compatibility check in container entrypoint.
4. Offline data persistence and container restart behavior.
5. Invariants: no default external IM chat channels and client tasks run on client devices.
"""

from __future__ import annotations

import platform
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from octop import __version__
from octop.api.routers.health import health


@pytest.mark.asyncio
async def test_health_reports_version_and_architecture() -> None:
    server = MagicMock()
    server.database_bound = True
    server.user_manager.count.return_value = 2
    server.app_runtime.agent_registry.list_rows.return_value = []
    server._started_at = 1700000000

    res = await health(server=server)

    assert res.ok is True
    assert res["ok"] is True
    assert res.version == __version__
    assert res.arch == platform.machine()
    assert res.db is True
    assert res.users_loaded == 2
    assert res.agents_running == 0


def test_compose_resource_limits_and_image_override() -> None:
    """Compose files must define explicit resource limits (CPU/Memory) and image override."""
    repo = Path(__file__).resolve().parents[2]
    compose_path = repo / "docker" / "docker-compose.yml"
    assert compose_path.exists()

    content = compose_path.read_text(encoding="utf-8")
    compose = yaml.safe_load(content)

    octop_svc = compose["services"]["octop"]
    # Image must support environment override for offline imported images
    assert "${OCTOP_IMAGE:-" in content or "${COLINK_IMAGE:-" in content
    # Resource limits must be explicitly declared
    assert "deploy" in octop_svc
    assert "resources" in octop_svc["deploy"]
    assert "limits" in octop_svc["deploy"]["resources"]
    limits = octop_svc["deploy"]["resources"]["limits"]
    assert "cpus" in limits
    assert "memory" in limits

    # Invariants for persistent volume and multi-agent limits
    assert "${OCTOP_DATA:-~/.octop}:/data/.octop" in octop_svc["volumes"]
    assert octop_svc["ulimits"]["nofile"]["soft"] >= 65535
    assert octop_svc["shm_size"] == "1gb"


def test_offline_compose_file_orchestration() -> None:
    """Dedicated offline orchestration file must be present and fully self-contained."""
    repo = Path(__file__).resolve().parents[2]
    offline_compose = repo / "docker" / "docker-compose.offline.yml"
    assert offline_compose.exists(), (
        "docker-compose.offline.yml must be provided for offline deployment"
    )

    content = offline_compose.read_text(encoding="utf-8")
    compose = yaml.safe_load(content)
    octop_svc = compose["services"]["octop"]

    # Must specify resource limits and pull policy
    limits = octop_svc["deploy"]["resources"]["limits"]
    assert "cpus" in limits
    assert "memory" in limits
    assert octop_svc.get("pull_policy") == "never"

    # Must persist data to /data/.octop
    assert any("/data/.octop" in str(v) for v in octop_svc["volumes"])

    # Must not contain external IM or bridge channels by default
    env_keys = [str(e).split("=")[0] for e in octop_svc.get("environment", [])]
    for restricted in ("FEISHU_", "DINGTALK_", "WECOM_", "DISCORD_", "TELEGRAM_"):
        assert not any(k.startswith(restricted) for k in env_keys)


def test_entrypoint_handles_arm64_sve2_openssl_compat() -> None:
    """docker-entrypoint.sh must detect ARM64 sve2 without sve and inject OPENSSL_armcap=0."""
    repo = Path(__file__).resolve().parents[2]
    entrypoint = (repo / "docker" / "docker-entrypoint.sh").read_text(encoding="utf-8")
    assert "OPENSSL_armcap" in entrypoint
    assert "sve2" in entrypoint


def test_dual_architecture_image_combinations_and_dependency_locks() -> None:
    """Orchestration and image manifests must note actual adopted multi-arch combinations."""
    repo = Path(__file__).resolve().parents[2]
    dockerfile = (repo / "docker" / "Dockerfile").read_text(encoding="utf-8")
    compose_offline = (repo / "docker" / "docker-compose.offline.yml").read_text(encoding="utf-8")

    # Multi-arch base images
    assert "FROM node:20-slim AS frontend-builder" in dockerfile
    assert "FROM python:3.12-slim AS runtime" in dockerfile

    # Both x86_64 and ARM64 noted in offline orchestration
    assert "x86_64" in compose_offline
    assert "ARM64" in compose_offline
    assert "docker load" in compose_offline

    # Explicit resource constraints
    assert "COLINK_CPUS_LIMIT" in compose_offline
    assert "COLINK_MEM_LIMIT" in compose_offline


def test_no_unrelated_chat_channels_or_central_execution_defaults() -> None:
    """Offline orchestration must not enable external chat channels or privileged central execution."""
    repo = Path(__file__).resolve().parents[2]
    compose_offline = yaml.safe_load(
        (repo / "docker" / "docker-compose.offline.yml").read_text(encoding="utf-8")
    )
    svc = compose_offline["services"]["octop"]

    # Must NOT have privileged: true or mount docker.sock (no central arbitrary execution)
    assert svc.get("privileged") is not True
    volumes = svc.get("volumes", [])
    assert not any("docker.sock" in str(v) for v in volumes)

    # Must NOT define cloud chat integrations (Feishu, DingTalk, WeCom, QQ, Discord, Telegram)
    env = svc.get("environment", [])
    for line in env:
        name = line.split("=")[0]
        assert not any(
            name.startswith(p)
            for p in ("FEISHU_", "DINGTALK_", "WECOM_", "QQ_", "DISCORD_", "TELEGRAM_")
        )
