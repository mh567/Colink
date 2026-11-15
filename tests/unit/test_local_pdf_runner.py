"""Exercise the PDF worker through its isolated process protocol."""

import base64
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

RUNNER = Path(__file__).resolve().parents[2] / "desktop/pdf/runner.py"


def run_worker(mode, data):
    result = subprocess.run(
        [os.environ.get("PDF_TEST_PYTHON", sys.executable), "-I", str(RUNNER), mode],
        input=data,
        capture_output=True,
        timeout=30,
    )
    assert result.stdout, result.stderr.decode(errors="replace")
    return result.returncode, json.loads(result.stdout)


def test_chinese_multipage_roundtrip():
    text = "项目进展 (2026) \\\n" + "完成验收 12 项\n" * 90 + "最后一页"
    code, result = run_worker("create", text.encode())
    assert code == 0
    document = base64.b64decode(result["document"], validate=True)
    reader = PdfReader(io.BytesIO(document))
    assert len(reader.pages) > 1
    code, result = run_worker("read", document)
    assert code == 0
    assert "项目进展 (2026) \\" in result["text"]
    assert "最后一页" in result["text"]
    assert result["text"].count("完成验收 12 项") == 90


def test_blank_and_encrypted_and_page_limit():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    assert run_worker("read", buf.getvalue())[1] == {"error": "PDF_NO_TEXT"}
    writer.encrypt("secret")
    buf = io.BytesIO()
    writer.write(buf)
    assert run_worker("read", buf.getvalue())[1] == {"error": "PDF_ENCRYPTED"}
    writer = PdfWriter()
    for _ in range(21):
        writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    assert run_worker("read", buf.getvalue())[1] == {"error": "PDF_PAGE_LIMIT"}


def test_scanned_page_is_not_silently_skipped():
    scan = Path(__file__).resolve().parents[1] / "fixtures/ocr/scanned.pdf"
    assert run_worker("read", scan.read_bytes())[1] == {"error": "PDF_NO_TEXT_PAGE"}


@pytest.mark.parametrize(
    ("mode", "data", "error"),
    [
        ("read", b"bad", "PDF_INVALID_SOURCE"),
        ("create", b"", "PDF_NO_TEXT"),
        ("create", b"x" * 60001, "PDF_TEXT_LIMIT"),
        ("create", "表情😀".encode(), "PDF_UNSUPPORTED_GLYPH"),
    ],
)
def test_errors(mode, data, error):
    code, result = run_worker(mode, data)
    assert code != 0
    assert result == {"error": error}


def test_missing_font_and_dependency_are_explicit(tmp_path):
    worker = tmp_path / "runner.py"
    worker.write_bytes(RUNNER.read_bytes())
    python = os.environ.get("PDF_TEST_PYTHON", sys.executable)
    result = subprocess.run(
        [python, "-I", str(worker), "create"], input=b"hello", capture_output=True
    )
    assert json.loads(result.stdout) == {"error": "PDF_FONT_MISSING"}
    result = subprocess.run(
        [python, "-I", "-S", str(worker), "read"], input=b"%PDF-1.4", capture_output=True
    )
    assert json.loads(result.stdout) == {"error": "PDF_RUNTIME_UNAVAILABLE"}


def test_network_audit_hook_denies_connections():
    python = os.environ.get("PDF_TEST_PYTHON", sys.executable)
    script = (
        "import importlib.util, socket, sys; "
        f"spec=importlib.util.spec_from_file_location('worker', {str(RUNNER)!r}); "
        "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
        "sys.addaudithook(m.deny_network); socket.socket()"
    )
    result = subprocess.run([python, "-I", "-c", script], capture_output=True)
    assert result.returncode != 0
    assert b"PDF_NETWORK_DENIED" in result.stderr


def test_text_with_blank_page_is_allowed():
    code, result = run_worker("create", "第一页".encode())
    assert code == 0
    reader = PdfReader(io.BytesIO(base64.b64decode(result["document"])))
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)
    code, result = run_worker("read", output.getvalue())
    assert code == 0
    assert "第一页" in result["text"]


def test_long_text_generates_more_than_twenty_pages():
    code, result = run_worker("create", ("这是内容。\n" * 1200).encode())
    assert code == 0
    reader = PdfReader(io.BytesIO(base64.b64decode(result["document"])))
    assert len(reader.pages) > 20
    assert "这是内容" in reader.pages[-1].extract_text()


def test_project_pypdf_reads_created_text_pdf():
    code, result = run_worker("create", "测试中文".encode())
    assert code == 0
    document = base64.b64decode(result["document"])
    result = subprocess.run(
        [sys.executable, "-I", str(RUNNER), "read"], input=document, capture_output=True, timeout=30
    )
    assert result.returncode == 0
    assert "测试中文" in json.loads(result.stdout)["text"]


def test_text_with_illustration_is_extracted():
    scan = Path(__file__).resolve().parents[1] / "fixtures/ocr/scanned.pdf"
    code, result = run_worker("create", "图片标题".encode())
    assert code == 0
    caption = PdfReader(io.BytesIO(base64.b64decode(result["document"]))).pages[0]
    source = PdfReader(str(scan))
    page = source.pages[0]
    page.merge_page(caption)
    writer = PdfWriter()
    writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    code, result = run_worker("read", output.getvalue())
    assert code == 0
    assert "图片标题" in result["text"]


def test_tabs_become_readable_spaces():
    code, result = run_worker("create", "项目\t数量\n验收\t12".encode())
    assert code == 0
    document = PdfReader(io.BytesIO(base64.b64decode(result["document"])))
    extracted = document.pages[0].extract_text()
    assert "项目  数量" in extracted
    assert "验收  12" in extracted


def test_blank_page_with_graphics_state_is_allowed():
    from pypdf.generic import DecodedStreamObject, NameObject

    code, result = run_worker("create", "有效文字".encode())
    assert code == 0
    source = PdfReader(io.BytesIO(base64.b64decode(result["document"])))
    writer = PdfWriter()
    writer.add_page(source.pages[0])
    blank = writer.add_blank_page(width=100, height=100)
    content = DecodedStreamObject()
    content.set_data(b"q 1 0 0 1 0 0 cm Q")
    blank[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    code, result = run_worker("read", output.getvalue())
    assert code == 0
    assert "有效文字" in result["text"]


def test_oversized_source_is_rejected():
    code, result = run_worker("read", b"%PDF-" + b"x" * (20 * 1024 * 1024))
    assert code != 0
    assert result == {"error": "PDF_FILE_LIMIT"}


def test_document_content_never_leaks_to_stderr():
    from pypdf.generic import DecodedStreamObject, NameObject

    code, result = run_worker("create", "Hello 中文".encode())
    assert code == 0
    writer = PdfWriter(clone_from=io.BytesIO(base64.b64decode(result["document"])))
    fonts = writer.pages[0]["/Resources"]["/Font"].get_object()
    for ref in fonts.values():
        font = ref.get_object()
        if "/ToUnicode" in font:
            cmap = (
                font["/ToUnicode"]
                .get_data()
                .replace(b"endbfchar", b"<zz> <SECRET-CUSTOMER-DATA>\nendbfchar", 1)
            )
            stream = DecodedStreamObject()
            stream.set_data(cmap)
            font[NameObject("/ToUnicode")] = writer._add_object(stream)
            break
    else:
        pytest.fail("fixture needs a ToUnicode mapping")
    output = io.BytesIO()
    writer.write(output)
    result = subprocess.run(
        [sys.executable, "-I", str(RUNNER), "read"],
        input=output.getvalue(),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert b"SECRET-CUSTOMER-DATA" not in result.stderr


def test_white_background_page_is_blank():
    python = os.environ.get("PDF_TEST_PYTHON", sys.executable)
    script = """import io,sys
from reportlab.pdfgen import canvas
out=io.BytesIO()
pdf=canvas.Canvas(out)
pdf.drawString(50, 700, 'Hello')
pdf.showPage()
pdf.setFillColorRGB(1, 1, 1)
pdf.rect(0, 0, 595, 842, fill=1, stroke=0)
pdf.showPage()
pdf.save()
sys.stdout.buffer.write(out.getvalue())
"""
    source = subprocess.run([python, "-I", "-c", script], capture_output=True, check=True).stdout
    code, result = run_worker("read", source)
    assert code == 0
    assert "Hello" in result["text"]


def _pdf_with_form(kind):
    python = os.environ.get("PDF_TEST_PYTHON", sys.executable)
    script = """import io,sys
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
out = io.BytesIO()
pdf = canvas.Canvas(out)
pdf.drawString(50, 700, 'Page one')
pdf.showPage()
pdf.beginForm('base')
if sys.argv[1] == 'image':
    pdf.drawImage(ImageReader(Image.new('RGB', (80, 40), 'black')), 0, 0, 80, 40)
else:
    pdf.setFillColorRGB(1, 1, 1)
    pdf.rect(0, 0, 595, 842, fill=1, stroke=0)
pdf.endForm()
name = 'base'
if sys.argv[1] == 'deep':
    for n in range(20):
        previous = name
        name = 'nested' + str(n)
        pdf.beginForm(name)
        pdf.doForm(previous)
        pdf.endForm()
pdf.doForm(name)
pdf.showPage()
pdf.save()
sys.stdout.buffer.write(out.getvalue())
"""
    return subprocess.run(
        [python, "-I", "-c", script, kind], capture_output=True, check=True
    ).stdout


def test_white_background_in_form_is_blank():
    code, result = run_worker("read", _pdf_with_form("blank"))
    assert code == 0
    assert "Page one" in result["text"]


def test_scanned_image_in_form_is_rejected():
    assert run_worker("read", _pdf_with_form("image"))[1] == {"error": "PDF_NO_TEXT_PAGE"}


def test_excessively_nested_forms_are_rejected():
    assert run_worker("read", _pdf_with_form("deep"))[1] == {"error": "PDF_INVALID_SOURCE"}


@pytest.mark.parametrize("in_form", [False, True])
def test_compressed_page_content_limit(in_form):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    stream = DecodedStreamObject()
    stream.set_data(b"q Q\n" * 70000)
    if in_form:
        stream[NameObject("/Subtype")] = NameObject("/Form")
        stream[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject()}
        )
        form = writer._add_object(stream.flate_encode())
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/XObject"): DictionaryObject({NameObject("/Fm"): form}),
                NameObject("/Font"): DictionaryObject(),
            }
        )
        stream = DecodedStreamObject()
        stream.set_data(b"/Fm Do")
    page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    output = io.BytesIO()
    writer.write(output)
    assert len(output.getvalue()) < 10000
    code, result = run_worker("read", output.getvalue())
    assert code != 0
    assert result == {"error": "PDF_CONTENT_LIMIT"}


def test_compressed_nested_form_uses_parent_resources():
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    big = DecodedStreamObject()
    big.set_data(b"q Q\n" * 70000)
    big[NameObject("/Subtype")] = NameObject("/Form")
    outer = DecodedStreamObject()
    outer.set_data(b"/Big Do")
    outer[NameObject("/Subtype")] = NameObject("/Form")
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject(
                {
                    NameObject("/Outer"): writer._add_object(outer.flate_encode()),
                    NameObject("/Big"): writer._add_object(big.flate_encode()),
                }
            ),
            NameObject("/Font"): DictionaryObject(),
        }
    )
    content = DecodedStreamObject()
    content.set_data(b"/Outer Do")
    page[NameObject("/Contents")] = writer._add_object(content.flate_encode())
    output = io.BytesIO()
    writer.write(output)
    assert len(output.getvalue()) < 10000
    code, result = run_worker("read", output.getvalue())
    assert code != 0
    assert result == {"error": "PDF_CONTENT_LIMIT"}


def test_explicit_null_page_content_is_blank():
    from pypdf.generic import NameObject, NullObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    page[NameObject("/Contents")] = NullObject()
    output = io.BytesIO()
    writer.write(output)
    code, result = run_worker("read", output.getvalue())
    assert code != 0
    assert result == {"error": "PDF_NO_TEXT"}
