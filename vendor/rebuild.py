"""Rebuild the two temporary Colink dependency wheels from their PyPI releases."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

VENDOR = Path(__file__).resolve().parent
# Source artifacts and digests from uv.lock before the local overrides.
SOURCES = {
    "octop-harness": (
        "https://files.pythonhosted.org/packages/6f/f5/ae9e96393557285832f75166d2c387f0c7b94e98cb8992dd043448c91444/octop_harness-1.0.7.tar.gz",
        "c74f0e979ed6b1a3272a911c0da2abe4825b632073573bdd211c375d2c4fad1b",
    ),
    "octop-memory": (
        "https://files.pythonhosted.org/packages/b8/7d/3247a1e58e88c1524fc112135832b3a26b9c17eec181c1f7f744b0a7b1cc/octop_memory-1.0.3.tar.gz",
        "cb7305462c7032aa013a95840c970e14a71b01ff14fe15a45434076eea8eb25c",
    ),
}


def build(name: str, url: str, digest: str) -> None:
    with urlopen(url, timeout=60) as response:
        source = response.read()
    if hashlib.sha256(source).hexdigest() != digest:
        raise ValueError(f"Unexpected source digest for {name}")

    with TemporaryDirectory(prefix=f"{name}-") as staging:
        root = Path(staging)
        with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as archive:
            archive.extractall(root, filter="data")
        (package,) = root.iterdir()
        subprocess.run(
            [
                "git",
                "apply",
                "--unidiff-zero",
                "--whitespace=error-all",
                str(VENDOR / f"{name}.patch"),
            ],
            cwd=package,
            check=True,
        )
        constraints = root / "build-constraints.txt"
        constraints.write_text("hatchling==1.33.0\n", encoding="utf-8")
        wheel_dir = root / "wheel"
        subprocess.run(
            [
                "uv",
                "build",
                "--wheel",
                "--python",
                "3.12",
                "--no-sources",
                "--build-constraints",
                str(constraints),
                "--out-dir",
                str(wheel_dir),
                str(package),
            ],
            env={**os.environ, "SOURCE_DATE_EPOCH": "1700000000"},
            check=True,
        )
        (wheel,) = wheel_dir.glob("*.whl")
        shutil.copyfile(wheel, VENDOR / wheel.name)


if __name__ == "__main__":
    for name, (url, digest) in SOURCES.items():
        build(name, url, digest)
