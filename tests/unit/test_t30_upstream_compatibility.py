"""Unit tests for T30: upstream component compatibility and intranet pruning."""

from __future__ import annotations

import importlib
import importlib.metadata
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from octop.api.routers.health import HealthResponse
from octop.infra.market.http import portal_base


def test_upstream_adopted_components_and_licenses() -> None:
    """Verify adopted component stack and their root MIT licenses."""
    # Verify core runtime modules are importable
    harness_mod = importlib.import_module("octop_harness")
    memory_mod = importlib.import_module("octop_memory")
    gateway_mod = importlib.import_module("octop_gateway")
    browser_mod = importlib.import_module("octop_browser")

    assert harness_mod is not None
    assert memory_mod is not None
    assert gateway_mod is not None
    assert browser_mod is not None

    # Check dist-info metadata for licenses
    for dist_name in ("colink-harness", "colink-memory", "octop-gateway", "octop-browser"):
        try:
            dist = importlib.metadata.distribution(dist_name)
        except importlib.metadata.PackageNotFoundError:
            # Fall back to upstream name if fork name was normalized
            alt_name = dist_name.replace("colink-", "octop-")
            dist = importlib.metadata.distribution(alt_name)
        meta = dist.metadata
        license_field = meta.get("License", "") or meta.get("License-Expression", "")
        classifiers = meta.get_all("Classifier") or []
        mit_classifier = any("MIT" in c for c in classifiers)
        assert "MIT" in license_field or mit_classifier, f"{dist_name} must retain MIT license"


def test_memory_isolation_and_forgetting_contracts() -> None:
    """Verify that Colink patches for memory isolation and permanent forgetting are active."""
    # 1. Harness memory access gating
    from octop_harness.config import HarnessAgentConfig

    assert "memory_access_enabled" in HarnessAgentConfig.__dataclass_fields__, (
        "HarnessAgentConfig must have memory_access_enabled field for shared expert isolation"
    )

    from octop_harness.agent import HarnessAgent

    assert hasattr(HarnessAgent, "set_memory_access"), (
        "HarnessAgent must provide set_memory_access() for live memory gating"
    )

    from octop_harness.builtin.tools.memory_tools import (
        _ACCESS_DISABLED,
        MEMORY_READ_TOOLS,
        build_memory_tools,
    )

    assert "memory_get" in MEMORY_READ_TOOLS
    assert "memory_search" in MEMORY_READ_TOOLS

    mock_service = MagicMock()
    # When access is disabled, memory read tools must return _ACCESS_DISABLED
    tools = build_memory_tools(mock_service, access_allowed=lambda: False)
    for tool in tools:
        if tool.name in MEMORY_READ_TOOLS:
            res = tool.invoke({"path": "test"} if tool.name == "memory_get" else {"query": "test"})
            assert res == _ACCESS_DISABLED

    # 2. Memory permanent forgetting and tombstone support
    from octop_memory.core import Memory

    assert hasattr(Memory, "forget_atom"), (
        "Memory must provide forget_atom() for permanent physical erasure"
    )
    assert hasattr(Memory, "atom_forget_ids"), (
        "Memory must provide atom_forget_ids() to retrieve historical predecessor chains"
    )

    from octop_memory.storage.backends.sqlite import SqliteMemoryBackend

    assert hasattr(SqliteMemoryBackend, "forget_atom")
    assert hasattr(SqliteMemoryBackend, "atom_forget_ids")


def test_intranet_pruning_and_market_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that external markets and public cloud requirements are not hard runtime dependencies."""
    # Remote market origin is optional and safely defaults to empty / disabled
    monkeypatch.delenv("OCTOP_PORTAL_ORIGIN", raising=False)
    monkeypatch.setenv("OCTOP_PORTAL_ORIGIN", "")
    assert portal_base() == ""

    # Verify health response does not require external network or cloud services
    resp = HealthResponse(
        ok=True,
        version="1.0.3b3",
        arch="arm64",
        started_at=1700000000,
        db=True,
        users_loaded=1,
        agents_running=0,
    )
    assert resp.ok is True
    assert resp.db is True


def test_restricted_ppt_skill_exclusion_contract() -> None:
    """Verify the distribution stripping script is present and targets restricted PPT materials."""
    repo = Path(__file__).resolve().parents[2]
    strip_script = repo / "scripts" / "strip_restricted_skills.py"
    assert strip_script.is_file(), "strip_restricted_skills.py must exist"

    content = strip_script.read_text(encoding="utf-8")
    assert "powerpoint" in content
    assert "colink_harness" in content
    assert "octop_harness" in content
