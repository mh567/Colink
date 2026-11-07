"""Exercise native wheel prefetch at the build-tool boundary without a network."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="fake executable uses a POSIX shebang")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("platform", ["darwin-arm64", "darwin-amd64", "windows-arm64"])
def test_native_cache_builds_wheels_with_locked_overrides(tmp_path, platform):
    executable = tmp_path / "uv"
    log = tmp_path / "calls.jsonl"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['CALL_LOG'], 'a') as f: f.write(json.dumps(args) + '\\n')\n"
        "if args[0] == 'export':\n"
        "    pathlib.Path(args[args.index('-o') + 1]).write_text('antlr4-python3-runtime==4.9.3\\ncryptography==49.0.0\\n')\n"
        "elif args[:2] == ['pip', 'compile']:\n"
        "    version = '49.0.0'\n"
        "    if '--overrides' in args:\n"
        "        overrides = pathlib.Path(args[args.index('--overrides') + 1]).read_text()\n"
        "        version = overrides.split('cryptography==')[1].strip()\n"
        "    pathlib.Path(args[args.index('--output-file') + 1]).write_text('antlr4-python3-runtime==4.9.3\\ncryptography==' + version + '\\n')\n"
        "elif args[:2] == ['tool', 'run'] and 'wheel' in args:\n"
        "    assert '--no-deps' in args and '--constraint' in args\n"
        "    directory = pathlib.Path(args[args.index('--wheel-dir') + 1])\n"
        "    (directory / 'antlr4_python3_runtime-4.9.3-py3-none-any.whl').touch()\n"
        "else: raise SystemExit('unsupported build command')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "GREEN_ROOT": str(tmp_path / "green"),
        "GREEN_HOST_PLAT": platform,
        "CALL_LOG": str(log),
    }
    result = subprocess.run(
        [shutil.which("bash"), str(ROOT / "desktop/portable/vendor-wheels.sh"), platform],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    export, compile_call, wheel = calls
    assert "--frozen" in export
    assert ("local-ocr" in export) == (platform == "darwin-arm64")
    assert "--no-deps" in compile_call
    assert "--no-deps" in wheel
    assert wheel[wheel.index("--only-binary") + 1] == "cryptography"
    pins = Path(wheel[wheel.index("--constraint") + 1]).read_text()
    expected = {"darwin-amd64": "46.0.3", "windows-arm64": "46.0.0"}.get(platform, "49.0.0")
    assert f"cryptography=={expected}" in pins
    assert list((tmp_path / "green/wheels" / platform).glob("antlr4*.whl"))


def test_cross_platform_prefetch_fails_before_downloading(tmp_path):
    env = {**os.environ, "GREEN_ROOT": str(tmp_path), "GREEN_HOST_PLAT": "darwin-arm64"}
    result = subprocess.run(
        [shutil.which("bash"), str(ROOT / "desktop/portable/vendor-wheels.sh"), "linux-arm64"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "native linux-arm64" in result.stderr
    assert not (tmp_path / "wheels").exists()
