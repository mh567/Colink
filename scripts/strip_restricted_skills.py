"""Remove restricted PowerPoint materials from an explicit release site-packages tree."""

from __future__ import annotations

import argparse
import csv
import shutil
import tempfile
from pathlib import Path

RESTRICTED_DIRS = (
    "octop_harness/builtin/skills/en/powerpoint",
    "octop_harness/builtin/skills/zh/powerpoint",
)


def strip_restricted_skills(site_packages: Path) -> None:
    if not site_packages.is_dir():
        raise ValueError(f"SITE_PACKAGES is not a directory: {site_packages}")
    if not (site_packages / "octop_harness").is_dir():
        raise ValueError(f"octop_harness installation is missing: {site_packages}")
    metadata = [path for path in site_packages.glob("octop_harness-*.dist-info") if path.is_dir()]
    if len(metadata) != 1:
        raise ValueError(
            f"Expected one octop-harness dist-info, found {len(metadata)}: {site_packages}"
        )
    record = metadata[0] / "RECORD"
    with record.open(encoding="utf-8", newline="") as stream:
        lines = stream.readlines()

    kept_lines: list[str] = []
    start = 0
    reader = csv.reader(lines, strict=True)
    for row in reader:
        if not row or not any(
            row[0] == path or row[0].startswith(f"{path}/") for path in RESTRICTED_DIRS
        ):
            kept_lines.extend(lines[start : reader.line_num])
        start = reader.line_num

    # Replace RECORD rather than editing uv's potentially hard-linked cache file.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=record.parent, prefix=".RECORD-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.writelines(kept_lines)
    try:
        shutil.copymode(record, temporary)
        for path in RESTRICTED_DIRS:
            restricted = site_packages / path
            if restricted.exists():
                shutil.rmtree(restricted)
        temporary.replace(record)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_packages", metavar="SITE_PACKAGES", type=Path)
    args = parser.parse_args()
    try:
        strip_restricted_skills(args.site_packages)
    except (OSError, ValueError, csv.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
