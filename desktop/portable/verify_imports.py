#!/usr/bin/env python3
"""Smoke-check a green packages/ tree against frozen requirements.

Usage:
  python desktop/portable/verify_imports.py \\
    --packages desktop/portable/release/Octop-<plat>/packages \\
    --requirements desktop/portable/requirements-<plat>.txt \\
    [--overrides desktop/portable/overrides-<plat>.txt]
"""

from __future__ import annotations

import argparse
import ast
import base64
import importlib
import json
import ntpath
import os
import posixpath
import re
import site
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

_REQ_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\s*==\s*(?P<ver>[^\s;#]+))?")

# Import names that have historically drifted across lock/install environments.
_SMOKE_IMPORTS = (
    "octop",
    "fastapi",
    "cryptography.fernet",
    "langchain_core",
    "langchain_openai",
    "pptx",
    "octop_browser",
    "pypdf",
    "pypdf._reader",
    "reportlab",
    "reportlab.pdfbase.ttfonts",
    "reportlab.pdfgen.canvas",
    "PIL",
    "charset_normalizer",
)


def _parse_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = _REQ_RE.match(line)
        if match is None:
            continue
        name = match.group("name").replace("_", "-").lower()
        ver = match.group("ver")
        if ver:
            pins[name] = ver
    return pins


def _parse_override_pins(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ";" in line:
            continue
        match = _REQ_RE.match(line)
        if match is None or not match.group("ver"):
            continue
        pins[match.group("name").replace("_", "-").lower()] = match.group("ver")
    return pins


def _installed_version(mod_name: str) -> str | None:
    try:
        mod = importlib.import_module(mod_name)
    except Exception:
        return None
    ver = getattr(mod, "__version__", None)
    if isinstance(ver, str):
        return ver
    return None


def _dist_version(dist_name: str) -> str | None:
    try:
        from importlib.metadata import version
    except ImportError:
        return None
    try:
        return version(dist_name)
    except Exception:
        return None


def _archive_destination(filename: str) -> str:
    relative = filename.partition("/")[2].replace("\\", "/")
    normalized = posixpath.normpath("/" + relative.lstrip("/"))
    return posixpath.normpath(
        "/".join(part.rstrip(" .") for part in normalized.split("/"))
    ).casefold()


def _has_current_resources(archive_path: Path, root_name: str, packages: Path) -> bool:
    root = packages.parent
    desktop = Path(__file__).resolve().parents[1]
    required = {
        "pptx/runner.py": desktop / "pptx/runner.py",
        "browser/runner.py": desktop / "browser/runner.py",
        "pdf/runner.py": desktop / "pdf/runner.py",
        "pdf/NotoSansSC-Regular.ttf": desktop / "pdf/NotoSansSC-Regular.ttf",
        "pdf/OFL.txt": desktop / "pdf/OFL.txt",
    }
    try:
        if any(
            (root / name).read_bytes() != source.read_bytes() for name, source in required.items()
        ):
            return False
        if (root / "PORTABLE-REVISION.txt").read_bytes() != b"4\n":
            return False
        if not (root / "runtime").is_dir() or not (root / "launch.py").is_file():
            return False
        expected = {}
        for relative in (
            "runtime",
            "packages",
            "pdf",
            *(["ocr"] if (root / "ocr").exists() else []),
        ):
            directory = root / relative
            if not directory.is_dir():
                return False
            for path in [directory, *directory.rglob("*")]:
                name = path.relative_to(root).as_posix()
                key = _archive_destination(f"{root_name}/{name}")
                if key in expected:
                    return False
                expected[key] = (name, path)
        standalone_files = (
            "launch.py",
            "pptx/runner.py",
            "browser/runner.py",
            "PORTABLE-REVISION.txt",
        )
        for name in standalone_files:
            expected[_archive_destination(f"{root_name}/{name}")] = (name, root / name)

        with zipfile.ZipFile(archive_path) as archive:
            seen = set()
            matched = set()
            entry_kinds = {}
            links = {}
            for member in archive.infolist():
                raw = member.filename.rstrip("/")
                parts = raw.split("/")
                if (
                    not parts
                    or parts[0] != root_name
                    or "\\" in raw
                    or any(part in ("", ".", "..") or part.rstrip(" .") != part for part in parts)
                ):
                    return False
                if len(parts) == 1:
                    kind = (
                        stat.S_IFMT(member.external_attr >> 16) if member.create_system == 3 else 0
                    )
                    if not member.is_dir() or kind not in (0, stat.S_IFDIR):
                        return False
                    continue
                key = _archive_destination(member.filename)
                if key in seen:
                    return False
                seen.add(key)
                mode = member.external_attr >> 16
                kind = stat.S_IFMT(mode) if member.create_system == 3 else 0
                if member.is_dir():
                    if kind not in (0, stat.S_IFDIR):
                        return False
                    entry_kinds[key] = "dir"
                elif kind == stat.S_IFLNK:
                    if member.file_size > 4096:
                        return False
                    target = os.fsdecode(archive.read(member))
                    resolved = posixpath.normpath(
                        posixpath.join(posixpath.dirname("/".join(parts[1:])), target)
                    )
                    if (
                        not target
                        or "\\" in target
                        or ntpath.isabs(target)
                        or ntpath.splitdrive(target)[0]
                        or resolved == ".."
                        or resolved.startswith("../")
                    ):
                        return False
                    entry_kinds[key] = "link"
                    links[key] = target
                elif kind in (0, stat.S_IFREG):
                    entry_kinds[key] = "file"
                else:
                    return False
                in_scope = (
                    parts[1] in ("runtime", "packages", "pdf", "ocr")
                    or "/".join(parts[1:]) in standalone_files
                )
                if not in_scope:
                    continue
                if key not in expected:
                    return False
                name, path = expected[key]
                if raw != f"{root_name}/{name}":
                    return False
                matched.add(key)
                if path.is_symlink():
                    target = os.readlink(path)
                    if (
                        kind != stat.S_IFLNK
                        or os.path.isabs(target)
                        or ntpath.isabs(target)
                        or ntpath.splitdrive(target)[0]
                        or "\\" in target
                        or not path.resolve().is_relative_to(root.resolve())
                        or member.file_size != len(os.fsencode(target))
                        or archive.read(member) != os.fsencode(target)
                    ):
                        return False
                elif path.is_dir():
                    if not member.is_dir() or kind not in (0, stat.S_IFDIR):
                        return False
                    if (
                        os.name != "nt"
                        and member.create_system == 3
                        and stat.S_IMODE(mode) != stat.S_IMODE(path.stat().st_mode)
                    ):
                        return False
                elif path.is_file():
                    if (
                        member.is_dir()
                        or kind not in (0, stat.S_IFREG)
                        or member.file_size != path.stat().st_size
                    ):
                        return False
                    if (
                        os.name != "nt"
                        and member.create_system == 3
                        and stat.S_IMODE(mode) != stat.S_IMODE(path.stat().st_mode)
                    ):
                        return False
                    with archive.open(member) as zipped, path.open("rb") as staged:
                        while chunk := staged.read(1024 * 1024):
                            if zipped.read(len(chunk)) != chunk:
                                return False
                        if zipped.read(1):
                            return False
                else:
                    return False
            for key in entry_kinds:
                parent = posixpath.dirname(key)
                while parent != "/":
                    if parent in entry_kinds and entry_kinds[parent] != "dir":
                        return False
                    parent = posixpath.dirname(parent)
            for key, target in links.items():
                stack = key.strip("/").split("/")[:-1]
                pending = target.split("/")
                hops = 0
                while pending:
                    part = pending.pop(0)
                    if part in ("", "."):
                        continue
                    if part == "..":
                        if not stack:
                            return False
                        stack.pop()
                        continue
                    stack.append(part)
                    link = "/" + "/".join(stack).casefold()
                    if link in links:
                        stack.pop()
                        pending = links[link].split("/") + pending
                        hops += 1
                        if hops > 40:
                            return False
            covered = set(matched)
            for key in matched:
                parent = posixpath.dirname(key)
                while parent != "/":
                    covered.add(parent)
                    parent = posixpath.dirname(parent)
            for key, (_name, path) in expected.items():
                if key not in covered and "__pycache__" not in path.relative_to(root).parts:
                    return False
            return True
    except (OSError, ValueError, RuntimeError, NotImplementedError, zipfile.BadZipFile):
        return False


def _verify_pdf_worker(python: Path, root: Path) -> bool:
    worker = root / "pdf/runner.py"
    sample = "便携 PDF 中文测试"
    try:
        created = subprocess.run(
            [str(python), "-I", "-B", str(worker), "create"],
            input=sample.encode("utf-8"),
            capture_output=True,
            timeout=30,
            check=True,
        )
        document = base64.b64decode(json.loads(created.stdout)["document"], validate=True)
        if not document.startswith(b"%PDF-"):
            return False
        read = subprocess.run(
            [str(python), "-I", "-B", str(worker), "read"],
            input=document,
            capture_output=True,
            timeout=30,
            check=True,
        )
        return sample in json.loads(read.stdout)["text"]
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", required=True, type=Path)
    parser.add_argument("--requirements", required=True, type=Path)
    parser.add_argument("--overrides", type=Path, default=None)
    parser.add_argument("--portable-zip", type=Path, default=None)
    args = parser.parse_args()

    packages = args.packages.resolve()
    if not packages.is_dir():
        print(f"packages/ missing: {packages}", file=sys.stderr)
        return 1
    if args.portable_zip and not _has_current_resources(
        args.portable_zip, args.packages.parent.name, packages
    ):
        print("Rebuild the portable package without --reuse-portable.", file=sys.stderr)
        return 1
    site.addsitedir(str(packages))
    sys.path.insert(0, str(packages))

    req_pins = _parse_pins(args.requirements)
    override_pins = _parse_override_pins(args.overrides)
    pins = {**req_pins, **override_pins}

    errors: list[str] = []
    for dist in (
        "langchain-core",
        "langchain-openai",
        "cryptography",
        "python-pptx",
        "octop-browser",
        "pypdf",
        "reportlab",
        "pillow",
        "charset-normalizer",
    ):
        expected = pins.get(dist)
        if expected is None:
            if dist in ("pypdf", "reportlab", "pillow", "charset-normalizer"):
                errors.append(f"{dist}: missing frozen requirement")
            continue
        got = _dist_version(dist)
        if got is None:
            errors.append(f"{dist}: not installed (want {expected})")
        elif got != expected:
            errors.append(f"{dist}: installed {got}, want {expected}")

    for mod in _SMOKE_IMPORTS:
        try:
            imported = importlib.import_module(mod)
            if mod.split(".")[0] in (
                "pypdf",
                "reportlab",
                "PIL",
                "charset_normalizer",
            ) and not Path(imported.__file__).resolve().is_relative_to(packages):
                errors.append(f"import {mod}: outside packages/")
        except Exception as exc:
            errors.append(f"import {mod}: {type(exc).__name__}: {exc}")

    if not _verify_pdf_worker(Path(sys.executable), packages.parent):
        errors.append("PDF worker roundtrip failed")

    # Sanity: launch.py must stay importable as a file (no compile errors).
    try:
        ast.parse(Path(__file__).read_text(encoding="utf-8"))
    except SyntaxError as exc:
        errors.append(f"self-parse: {exc}")

    if errors:
        print("verify_imports FAILED:", file=sys.stderr)
        for item in errors:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("verify_imports OK")
    langchain_core_ver = _installed_version("langchain_core")
    if langchain_core_ver:
        print(f"langchain_core={langchain_core_ver}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
