"""Distribution builds remove restricted skills from installed dependencies."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_prune_installed_materials_preserves_other_skills_and_licenses(tmp_path: Path) -> None:
    packages = tmp_path / "packages"
    removed = (
        "octop_harness/builtin/skills/en/powerpoint/SKILL.md",
        "octop_harness/builtin/skills/en/powerpoint/scripts/render.py",
        "octop_harness/builtin/skills/zh/powerpoint/LICENSE.txt",
    )
    retained = (
        "octop_harness/builtin/skills/en/spreadsheets/SKILL.md",
        "octop_harness/builtin/skills/en/powerpoint-notes/SKILL.md",
        "octop_harness-1.0.4.dist-info/licenses/LICENSE",
        "pptx/__init__.py",
    )
    for relative in (*removed, *retained):
        path = packages / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    script = Path(__file__).resolve().parents[3] / "tools/prune_runtime_materials.py"

    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(script), str(packages)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert not (packages / "octop_harness/builtin/skills/en/powerpoint").exists()
        assert not (packages / "octop_harness/builtin/skills/zh/powerpoint").exists()
        for relative in retained:
            assert (packages / relative).read_text(encoding="utf-8") == relative
