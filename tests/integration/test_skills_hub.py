"""Integration tests for GET /api/agents/{id}/skills/hub/search
and POST /api/agents/{id}/skills/hub/install."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest


@pytest.fixture
async def env(env_with_main_agent):
    yield env_with_main_agent


# --- auth guard tests -------------------------------------------------------


async def test_hub_search_requires_auth(env: Any) -> None:
    c, _srv, _auth, aid = env
    r = await c.get(f"/api/agents/{aid}/skills/hub/search")
    assert r.status_code not in (200,), f"Expected auth failure, got {r.status_code}"


async def test_hub_install_requires_auth(env: Any) -> None:
    c, _srv, _auth, aid = env
    r = await c.post(
        f"/api/agents/{aid}/skills/hub/install",
        json={"skill_name": "file-reader", "enable": True},
    )
    assert r.status_code not in (200, 201), f"Expected auth failure, got {r.status_code}"


@pytest.mark.parametrize(
    ("endpoint", "params", "network_function"),
    [
        ("search", {}, "search_skillhub"),
        ("search", {"q": "file-reader"}, "search_skillhub"),
        ("rankings", {"type": "hot"}, "fetch_skillhub_rankings"),
    ],
)
async def test_hub_market_rejected_before_network(
    env: Any,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    params: dict[str, str],
    network_function: str,
) -> None:
    from octop.infra.skills import skillhub_market

    network = Mock(side_effect=AssertionError("Online market must not run"))
    monkeypatch.setattr(skillhub_market, network_function, network)
    c, _srv, auth, aid = env
    r = await c.get(f"/api/agents/{aid}/skills/hub/{endpoint}", headers=auth, params=params)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "LOCAL_EXPERT_REVIEW_REQUIRED"
    network.assert_not_called()


@pytest.mark.parametrize(
    "skill_name",
    ["nonexistent-xyz-skill-abc123", "stable-english-slug", "fallback-skill", "unsafe-package"],
)
async def test_hub_install_rejected_before_network(
    env: Any,
    monkeypatch: pytest.MonkeyPatch,
    skill_name: str,
) -> None:
    from octop.infra.skills import install, skillhub_market

    network = Mock(side_effect=AssertionError("Online download must not run"))
    installer = Mock(side_effect=AssertionError("Online installer must not run"))
    monkeypatch.setattr(skillhub_market, "download_skillhub_package", network)
    monkeypatch.setattr(install, "install_skill_from_skillhub", installer)
    c, _srv, auth, aid = env
    r = await c.post(
        f"/api/agents/{aid}/skills/hub/install",
        headers=auth,
        json={
            "skill_name": skill_name,
            "enable": True,
            "display_name": "中文展示名称",
            "icon_url": "https://cdn.example.com/skill.png",
        },
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "LOCAL_EXPERT_REVIEW_REQUIRED"
    network.assert_not_called()
    installer.assert_not_called()
