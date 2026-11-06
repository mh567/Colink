"""The native OCR subprocess accepts bytes, never filenames or network URLs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[2] / "desktop" / "ocr" / "runner.py"


def test_runner_rejects_invalid_pdf_without_producing_text():
    result = subprocess.run(
        [sys.executable, "-I", str(RUNNER)],
        input=b"not a PDF",
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    reply = json.loads(result.stdout)
    assert reply == {"error": "OCR_INVALID_PDF"}


def test_runner_does_not_accept_file_paths(tmp_path):
    source = tmp_path / "private.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    result = subprocess.run(
        [sys.executable, "-I", str(RUNNER), str(source)], input=b"", capture_output=True, timeout=10
    )
    assert result.returncode != 0
    assert "OCR_INVALID_ARGS" in json.loads(result.stdout)["error"]


def test_runner_rejects_missing_models_without_downloading():
    result = subprocess.run(
        [sys.executable, "-I", str(RUNNER)], input=b"%PDF-1.7\n", capture_output=True, timeout=10
    )
    assert result.returncode != 0
    assert "OCR_MODELS_MISSING" in json.loads(result.stdout)["error"]


def test_runner_rejects_oversized_input():
    result = subprocess.run(
        [sys.executable, "-I", str(RUNNER)],
        input=b"%PDF-" + b" " * (20 * 1024 * 1024),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "OCR_FILE_LIMIT" in json.loads(result.stdout)["error"]


# Set this only to a prepared portable root. This runs the shipped interpreter,
# renderer and models, rather than stubbing the OCR engine.
def native_run(data):
    import os

    import pytest

    root = os.environ.get("COLINK_TEST_OCR_ROOT")
    if not root:
        pytest.skip("requires prepared native OCR runtime (COLINK_TEST_OCR_ROOT)")
    root = Path(root)
    python = root / "runtime" / ("python.exe" if sys.platform == "win32" else "bin/python3")
    result = subprocess.run(
        [str(python), "-I", str(root / "ocr/runner.py")],
        input=data,
        capture_output=True,
        timeout=180,
    )
    return result.returncode, json.loads(result.stdout)


def blank_pdf(count=1, size=300):
    # Minimal fixture: no fonts, JavaScript, images or external resource references.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Count {count} /Kids [".encode()
        + b" ".join(f"{i + 3} 0 R".encode() for i in range(count))
        + b"] >>",
    ]
    objects += [
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {size} {size}] /Resources << >> >>".encode()
    ] * count
    data = bytearray(b"%PDF-1.7\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    )
    return bytes(data)


def test_native_scanned_pdf_preserves_key_content_and_page_numbers():
    status, reply = native_run((RUNNER.parents[2] / "tests/fixtures/ocr/scanned.pdf").read_bytes())
    assert status == 0
    for expected in (
        "采购会议记录",
        "CL2026",
        "12500",
        "张三",
        "2026年11月20日",
        "第 1 页",
        "第 2 页",
    ):
        assert expected in reply["text"]


def test_native_blank_page_is_a_failure():
    status, reply = native_run(blank_pdf())
    assert status != 0
    assert "OCR_PAGE_EMPTY" in reply["error"]


def test_native_corrupt_pdf_is_a_failure():
    status, reply = native_run(b"%PDF-1.7\ncorrupt")
    assert status != 0
    assert "OCR_PARSE_FAILED" in reply["error"]


def test_native_page_limit_is_not_silently_truncated():
    status, reply = native_run(blank_pdf(21))
    assert status != 0
    assert "OCR_PAGE_LIMIT" in reply["error"]


def test_native_oversized_page_is_not_rendered():
    status, reply = native_run(blank_pdf(size=100000))
    assert status != 0
    assert "OCR_PAGE_SIZE" in reply["error"]


def test_native_password_protected_pdf_is_a_failure():
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.encrypt("test-password")
    stream = io.BytesIO()
    writer.write(stream)
    status, reply = native_run(stream.getvalue())
    assert status != 0
    assert "OCR_PARSE_FAILED" in reply["error"]


def test_corrupt_model_is_reported_without_downloading(tmp_path):
    import os
    import shutil

    import pytest

    root = os.environ.get("COLINK_TEST_OCR_ROOT")
    if not root:
        pytest.skip("requires prepared native OCR runtime (COLINK_TEST_OCR_ROOT)")
    root = Path(root)
    models = tmp_path / "models"
    shutil.copytree(root / "ocr/models", models)
    (models / "PP-OCRv6_det_small.onnx").write_bytes(b"corrupt model")
    code = (
        "import sys,runpy; from pathlib import Path; "
        "sys.path.insert(0,sys.argv[1]); r=runpy.run_path(sys.argv[2]); "
        "sys.addaudithook(r['deny_network']); "
        "r['extract'](sys.stdin.buffer.read(),Path(sys.argv[3]))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(root / "packages"), str(RUNNER), str(models)],
        input=(RUNNER.parents[2] / "tests/fixtures/ocr/scanned.pdf").read_bytes(),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "OCR_RECOGNITION_FAILED" in result.stderr.decode("utf-8")
    assert not result.stdout
