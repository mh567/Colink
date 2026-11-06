"""Release staging removes restricted skills without changing other installed materials."""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
import zipfile
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


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only packaging shell script")
def test_offline_portable_packaging_reuses_its_python(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for interpreter discovery")
    repo = SCRIPT.parent.parent
    checkout = tmp_path / "checkout with spaces"
    portable = checkout / "desktop" / "portable"
    portable.mkdir(parents=True)
    for name in ("package.sh", "_common.sh"):
        shutil.copyfile(repo / "desktop" / "portable" / name, portable / name)
    shutil.copytree(repo / "desktop" / "portable" / "templates", portable / "templates")
    ocr = checkout / "desktop" / "ocr"
    ocr.mkdir()
    # The model boundary is stubbed here; real OCR is exercised by test_local_ocr_runner.
    (ocr / "prepare.py").write_text(
        """import os
import sys
from pathlib import Path
root = Path(sys.argv[1])
assert sys.argv[2:] == ["--python", str(root / "runtime/bin/python3")]
assert os.environ.get("TEST_BUNDLED_PYTHON") == "1"
(root / "ocr").mkdir()
(root / "ocr/fixture-marker.txt").write_text("bundled-python", encoding="utf-8")
""",
        encoding="utf-8",
    )
    (checkout / "scripts").mkdir()
    shutil.copyfile(SCRIPT, checkout / "scripts" / SCRIPT.name)
    (checkout / "pyproject.toml").write_text('version = "1.0.3b3"\n', encoding="utf-8")

    green = tmp_path / "green"
    runtime = green / "runtimes" / "linux-amd64" / "bin"
    runtime.mkdir(parents=True)
    python = runtime / "python3"
    python.write_text(
        '#!/bin/sh\nTEST_BUNDLED_PYTHON=1 exec "$TEST_PYTHON" "$@"\n', encoding="utf-8"
    )
    python.chmod(0o755)
    wheels = green / "wheels" / "linux-amd64"
    wheels.mkdir(parents=True)
    for name in [*(f"cached-{index}.whl" for index in range(10)), "uv-0.12.22-test.whl"]:
        (wheels / name).touch()
    site_packages, _ = _installation(tmp_path)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    installer = bin_dir / "uv"
    # Keep Python discovery real; only replace downloads and dependency installation.
    installer.write_text(
        """#!/bin/sh
exec "$TEST_PYTHON" - "$@" <<'PY'
import os
from pathlib import Path
import shutil
import sys
args = sys.argv[1:]
if args[0] == "build":
    output = Path(args[args.index("--out-dir") + 1])
    output.mkdir(parents=True, exist_ok=True)
    (output / "octop-1.0.3b3-py3-none-any.whl").touch()
elif args[0] == "export":
    Path(args[args.index("-o") + 1]).touch()
elif args[:2] == ["pip", "install"]:
    target = Path(args[args.index("--target") + 1])
    shutil.copytree(os.environ["TEST_PACKAGES"], target, dirs_exist_ok=True)
elif args[0] == "run":
    os.execv(os.environ["REAL_UV"], [os.environ["REAL_UV"], *args])
else:
    raise SystemExit("Unexpected uv command: " + str(args))
PY
""",
        encoding="utf-8",
    )
    installer.chmod(0o755)
    env = dict(os.environ)
    for key in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        env.pop(key, None)
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        TEST_PYTHON=sys.executable,
        TEST_PACKAGES=str(site_packages),
        REAL_UV=uv,
        GREEN_ROOT=str(green),
        GREEN_HOST_PLAT="linux-amd64",
        OCTOP_GREEN_OFFLINE="1",
        UV_CACHE_DIR=str(tmp_path / "uv-cache"),
        UV_PYTHON=sys.executable,
        UV_PYTHON_INSTALL_DIR=str(tmp_path / "no-managed-python"),
        UV_PYTHON_PREFERENCE="only-managed",
        UV_PYTHON_DOWNLOADS="never",
    )

    result = subprocess.run(
        ["bash", str(portable / "package.sh"), "linux-amd64"],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    archive = green / "release" / "Octop-portable-linux-amd64-1.0.3b3.zip"
    with zipfile.ZipFile(archive) as packed:
        assert packed.read("Octop-linux-amd64/ocr/fixture-marker.txt") == b"bundled-python"
        assert not any("/powerpoint/" in name for name in packed.namelist())
        assert packed.read(f"Octop-linux-amd64/packages/{DIST_INFO}/licenses/LICENSE") == (
            b"MIT license must remain\n"
        )
