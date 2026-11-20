"""Health probe and offline Compose configuration checks."""

from __future__ import annotations

import platform
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from octop import __version__
from octop.api.routers.health import health
from octop.infra.market.http import portal_base


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


def test_offline_compose_disables_remote_market(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load(
        (repo / "docker" / "docker-compose.offline.yml").read_text(encoding="utf-8")
    )
    env = dict(item.split("=", 1) for item in compose["services"]["octop"]["environment"])
    monkeypatch.delenv("OCTOP_PORTAL_ORIGIN", raising=False)
    if "OCTOP_PORTAL_ORIGIN" in env:
        monkeypatch.setenv("OCTOP_PORTAL_ORIGIN", env["OCTOP_PORTAL_ORIGIN"])
    assert portal_base() == ""


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
    assert octop_svc["ulimits"]["nofile"] == {"soft": 65535, "hard": 65535}
    assert octop_svc["shm_size"] == "1gb"
    assert octop_svc.get("pull_policy") == "never"
    assert "build" not in octop_svc

    # Must persist data to /data/.octop
    assert any("/data/.octop" in str(v) for v in octop_svc["volumes"])

    # Must not contain external IM or bridge channels by default
    env_keys = [str(e).split("=")[0] for e in octop_svc.get("environment", [])]
    for restricted in ("FEISHU_", "DINGTALK_", "WECOM_", "DISCORD_", "TELEGRAM_"):
        assert not any(k.startswith(restricted) for k in env_keys)


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
