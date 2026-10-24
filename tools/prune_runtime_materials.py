#!/usr/bin/env python3
"""Remove restricted Harness skills from installed distribution dependencies."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def prune(packages: Path) -> None:
    packages = packages.resolve(strict=True)
    for target in (packages / "octop_harness/builtin/skills").glob("*/powerpoint"):
        if target.is_symlink() or not target.resolve().is_relative_to(packages):
            raise ValueError(f"Use a packages directory without external skill links: {target}")
        shutil.rmtree(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages", type=Path, help="Installed site-packages directory")
    args = parser.parse_args()
    if not args.packages.is_dir():
        parser.error("Provide an existing site-packages directory")
    prune(args.packages)


if __name__ == "__main__":
    main()
