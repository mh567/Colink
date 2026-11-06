"""Assemble the OCR resources from installed, locked wheels; never download models."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from runner import MODELS


def prepare(root: Path) -> None:
    source = Path(__file__).resolve().parent
    target = root / "ocr"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "runner.py", target / "runner.py")
    shutil.copytree(source / "licenses", target / "licenses", dirs_exist_ok=True)
    models = target / "models"
    models.mkdir(exist_ok=True)
    for name in MODELS.values():
        model = root / "packages" / "rapidocr" / "models" / name
        if not model.is_file() or model.stat().st_size == 0:
            raise RuntimeError(f"缺少锁定 wheel 内的离线模型：{name}")
        shutil.copy2(model, models / name)


def verify(root: Path, python: str) -> None:
    sample = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "ocr" / "scanned.pdf"
    process = subprocess.run(
        [python, "-I", str(root / "ocr" / "runner.py")],
        input=sample.read_bytes(),
        capture_output=True,
        timeout=180,
    )
    if process.returncode:
        raise RuntimeError(f"离线 OCR 验证失败：{process.stdout.decode('utf-8', errors='replace')}")
    text = json.loads(process.stdout)["text"]
    for expected in (
        "采购会议记录",
        "CL2026",
        "12500",
        "张三",
        "2026年11月20日",
        "第 1 页",
        "第 2 页",
    ):
        if expected not in text:
            raise RuntimeError(f"离线 OCR 样本缺少关键内容：{expected}")
    print("[ocr] offline sample passed (2 pages)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--python", required=True, help="Native bundled interpreter for the smoke test"
    )
    args = parser.parse_args()
    prepare(args.root)
    verify(args.root, args.python)


if __name__ == "__main__":
    main()
