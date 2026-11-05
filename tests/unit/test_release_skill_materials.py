"""Release staging removes restricted skills without changing other installed materials."""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "strip_restricted_skills.py"
DIST_INFO = "octop_harness-1.0.7.dist-info"
KEPT_RECORD = (
    "octop_harness/__init__.py,,\r\n"
    '"octop_harness/builtin/skills/en/keep,skill/SKILL.md",sha256=unchanged,17\r\n'
    "octop_harness/builtin/skills/en/powerpoint-extra/SKILL.md,,\r\n"
    f"{DIST_INFO}/METADATA,,\r\n"
    f"{DIST_INFO}/licenses/LICENSE,,\r\n"
    f"{DIST_INFO}/RECORD,,\r\n"
).encode()


def _installation(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    site_packages = tmp_path / "packages with spaces"
    retained = {
        "octop_harness/__init__.py": b"",
        "octop_harness/builtin/skills/en/keep,skill/SKILL.md": b"keep this skill\n",
        "octop_harness/builtin/skills/en/powerpoint-extra/SKILL.md": b"separate skill\n",
        f"{DIST_INFO}/METADATA": b"Name: octop-harness\nVersion: 1.0.7\nLicense: MIT\n",
        f"{DIST_INFO}/licenses/LICENSE": b"MIT license must remain\n",
        "another_component-1.0.dist-info/NOTICE": b"keep third-party notice\n",
    }
    restricted = {
        "octop_harness/builtin/skills/en/powerpoint/SKILL.md": b"prompt fixture\n",
        "octop_harness/builtin/skills/en/powerpoint/LICENSE.txt": b"restricted fixture\n",
        "octop_harness/builtin/skills/en/powerpoint/scripts/nested/tool.py": b"script fixture\n",
        "octop_harness/builtin/skills/en/powerpoint/assets/slide.xml": b"asset fixture\n",
        "octop_harness/builtin/skills/zh/powerpoint/SKILL.md": b"translated prompt fixture\n",
    }
    for relative, content in (retained | restricted).items():
        path = site_packages / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    record = site_packages / DIST_INFO / "RECORD"
    record.write_bytes(KEPT_RECORD + "".join(f"{path},,\r\n" for path in restricted).encode())
    retained[f"{DIST_INFO}/RECORD"] = KEPT_RECORD
    return site_packages, retained


def _run(site_packages: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), str(site_packages)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_cli_removes_complete_trees_and_preserves_other_materials(tmp_path: Path) -> None:
    site_packages, retained = _installation(tmp_path)

    for _ in range(2):
        result = _run(site_packages)
        assert result.returncode == 0, result.stderr
        actual = {
            path.relative_to(site_packages).as_posix(): path.read_bytes()
            for path in site_packages.rglob("*")
            if path.is_file()
        }
        assert actual == retained
        assert not (site_packages / "octop_harness/builtin/skills/en/powerpoint").exists()
        assert not (site_packages / "octop_harness/builtin/skills/zh/powerpoint").exists()
        with (site_packages / DIST_INFO / "RECORD").open(encoding="utf-8", newline="") as stream:
            assert all((site_packages / row[0]).is_file() for row in csv.reader(stream))


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only hardlink semantics")
def test_cli_does_not_change_a_shared_cache_record(tmp_path: Path) -> None:
    site_packages, _ = _installation(tmp_path)
    record = site_packages / DIST_INFO / "RECORD"
    cached_record = tmp_path / "cached-RECORD"
    cached_record.hardlink_to(record)
    original = cached_record.read_bytes()

    result = _run(site_packages)

    assert result.returncode == 0, result.stderr
    assert cached_record.read_bytes() == original
    assert record.read_bytes() == KEPT_RECORD


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("tree", "SITE_PACKAGES is not a directory"),
        ("package", "octop_harness installation is missing"),
        ("metadata", "Expected one octop-harness dist-info, found 0"),
        ("unique-metadata", "Expected one octop-harness dist-info, found 2"),
        ("record", "RECORD"),
    ],
)
def test_cli_rejects_incomplete_or_ambiguous_installations(
    tmp_path: Path, missing: str, message: str
) -> None:
    site_packages, _ = _installation(tmp_path)
    if missing == "tree":
        shutil.rmtree(site_packages)
    elif missing == "package":
        shutil.rmtree(site_packages / "octop_harness")
    elif missing == "metadata":
        shutil.rmtree(site_packages / DIST_INFO)
    elif missing == "unique-metadata":
        (site_packages / "octop_harness-9.0.dist-info").mkdir()
    else:
        (site_packages / DIST_INFO / "RECORD").unlink()
    before = {path: path.read_bytes() for path in site_packages.rglob("*") if path.is_file()}

    result = _run(site_packages)

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert message in result.stderr
    assert {
        path: path.read_bytes() for path in site_packages.rglob("*") if path.is_file()
    } == before
