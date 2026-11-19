"""Tests for octop.infra.utils.arm64_compat."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

import pytest

from octop.infra.utils.arm64_compat import ensure_arm64_openssl_compat


def test_noop_on_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("OPENSSL_armcap", raising=False)
    ensure_arm64_openssl_compat()
    assert "OPENSSL_armcap" not in os.environ


def test_noop_on_non_arm64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.delenv("OPENSSL_armcap", raising=False)
    ensure_arm64_openssl_compat()
    assert "OPENSSL_armcap" not in os.environ


def test_sets_armcap_when_sve2_without_sve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text(
        "Features : fp asimd aes pmull sha1 sha2 crc32 atomics sve2 flagm2\n", encoding="utf-8"
    )

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    monkeypatch.delenv("OPENSSL_armcap", raising=False)

    ensure_arm64_openssl_compat(cpuinfo_path=cpuinfo)
    assert os.environ.get("OPENSSL_armcap") == "0x3f"  # noqa: SIM112


def test_noop_when_sve_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text(
        "Features : fp asimd aes pmull sha1 sha2 crc32 atomics sve sve2\n", encoding="utf-8"
    )

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    monkeypatch.delenv("OPENSSL_armcap", raising=False)

    ensure_arm64_openssl_compat(cpuinfo_path=cpuinfo)
    assert "OPENSSL_armcap" not in os.environ


def test_respects_existing_armcap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("Features : sve2\n", encoding="utf-8")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    monkeypatch.setenv("OPENSSL_armcap", "0")

    ensure_arm64_openssl_compat(cpuinfo_path=cpuinfo)
    assert os.environ.get("OPENSSL_armcap") == "0"  # noqa: SIM112
