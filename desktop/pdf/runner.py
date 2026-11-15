"""Offline text PDF worker; the only document input is stdin."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import sys
from pathlib import Path

FILE_LIMIT = 20 * 1024 * 1024
TEXT_LIMIT = 60000
PAGE_LIMIT = 20
CONTENT_LIMIT = 256 * 1024
FONT_PATH = Path(__file__).resolve().parent / "NotoSansSC-Regular.ttf"


class PdfError(Exception):
    pass


def deny_network(event: str, _args: tuple[object, ...]) -> None:
    if event.startswith("socket."):
        raise PdfError("PDF_NETWORK_DENIED")


def _has_unreadable_content(content, resources, reader, depth=0) -> bool:
    from pypdf.generic import ContentStream

    if depth >= 16:
        raise PdfError("PDF_INVALID_SOURCE")
    resources = resources.get_object() if hasattr(resources, "get_object") else resources
    for operands, operator in ContentStream(content, reader).operations:
        if operator in {b"Tj", b"TJ", b"'", b'"', b"INLINE IMAGE"}:
            return True
        if operator == b"Do":
            obj = resources["/XObject"][operands[0]].get_object()
            if obj.get("/Subtype") == "/Image":
                return True
            if obj.get("/Subtype") != "/Form":
                raise PdfError("PDF_INVALID_SOURCE")
            if _has_unreadable_content(obj, obj.get("/Resources", resources), reader, depth + 1):
                return True
    return False


def _check_content_budget(content, resources, reader, budget, depth=0):
    from pypdf.generic import ArrayObject, ContentStream, NullObject

    if depth >= 16:
        raise PdfError("PDF_INVALID_SOURCE")
    content = content.get_object()
    if isinstance(content, NullObject):
        return
    streams = content if isinstance(content, ArrayObject) else [content]
    for stream in streams:
        stream = stream.get_object()
        if isinstance(stream, NullObject):
            continue
        budget[0] -= len(stream.get_data())
        if budget[0] < 0:
            raise PdfError("PDF_CONTENT_LIMIT")
    resources = resources.get_object() if hasattr(resources, "get_object") else resources
    for operands, operator in ContentStream(content, reader).operations:
        if operator == b"Do":
            obj = resources["/XObject"][operands[0]].get_object()
            if obj.get("/Subtype") != "/Image":
                form_resources = obj.get("/Resources", resources)
                if hasattr(form_resources, "get_object"):
                    form_resources = form_resources.get_object()
                _check_content_budget(obj, form_resources, reader, budget, depth + 1)
                inherited = obj.get_inherited("/Resources", form_resources)
                if inherited is not form_resources:
                    _check_content_budget(obj, inherited, reader, budget, depth + 1)


def read_document(data: bytes) -> str:
    from pypdf import PdfReader

    if len(data) > FILE_LIMIT:
        raise PdfError("PDF_FILE_LIMIT")
    if not data.startswith(b"%PDF-"):
        raise PdfError("PDF_INVALID_SOURCE")
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise PdfError("PDF_ENCRYPTED")
        if not 1 <= len(reader.pages) <= PAGE_LIMIT:
            raise PdfError("PDF_PAGE_LIMIT")
        pages = []
        for page in reader.pages:
            content = page.get("/Contents")
            if content is not None:
                _check_content_budget(content, page.get_inherited("/Resources", {}), reader, [CONTENT_LIMIT])
            text = page.extract_text() or ""
            content = page.get_contents()
            if (
                not text.strip()
                and content is not None
                and _has_unreadable_content(content, page.get("/Resources", {}), reader)
            ):
                raise PdfError("PDF_NO_TEXT_PAGE")
            pages.append(text)
            if sum(map(len, pages)) + len(pages) - 1 > TEXT_LIMIT:
                raise PdfError("PDF_TEXT_LIMIT")
        result = "\n".join(pages)
        if not result.strip():
            raise PdfError("PDF_NO_TEXT")
        return result
    except PdfError:
        raise
    except Exception:
        raise PdfError("PDF_INVALID_SOURCE") from None


def create_document(data: bytes) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise PdfError("PDF_INVALID_TEXT") from None
    if len(text) > TEXT_LIMIT:
        raise PdfError("PDF_TEXT_LIMIT")
    if not text.strip():
        raise PdfError("PDF_NO_TEXT")
    text = text.expandtabs(4)
    if not FONT_PATH.is_file():
        raise PdfError("PDF_FONT_MISSING")
    try:
        font = TTFont("ColinkNotoSansSC", str(FONT_PATH))
    except Exception:
        raise PdfError("PDF_FONT_MISSING") from None
    if any(c not in "\n\r" and ord(c) not in font.face.charWidths for c in text):
        raise PdfError("PDF_UNSUPPORTED_GLYPH")
    pdfmetrics.registerFont(font)
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4, pageCompression=1)
    width, height = A4
    margin, size, leading = 48, 11, 16
    y = height - margin
    pdf.setFont(font.fontName, size)
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = ""
        line_width = 0.0
        lines = []
        for char in paragraph:
            char_width = pdfmetrics.stringWidth(char, font.fontName, size)
            if line and line_width + char_width > width - 2 * margin:
                lines.append(line)
                line, line_width = "", 0.0
            line += char
            line_width += char_width
        lines.append(line)
        for line in lines:
            if y < margin:
                pdf.showPage()
                pdf.setFont(font.fontName, size)
                y = height - margin
            pdf.drawString(margin, y, line)
            y -= leading
    pdf.save()
    result = output.getvalue()
    if len(result) > FILE_LIMIT:
        raise PdfError("PDF_FILE_LIMIT")
    return result


def main() -> int:
    sys.addaudithook(deny_network)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))
    try:
        if sys.argv[1:] == ["read"]:
            reply = {"text": read_document(sys.stdin.buffer.read(FILE_LIMIT + 1))}
        elif sys.argv[1:] == ["create"]:
            data = sys.stdin.buffer.read(TEXT_LIMIT * 4 + 1)
            if len(data) > TEXT_LIMIT * 4:
                raise PdfError("PDF_TEXT_LIMIT")
            reply = {"document": base64.b64encode(create_document(data)).decode("ascii")}
        else:
            raise PdfError("PDF_TASK_FAILED")
        status = 0
    except ImportError:
        reply, status = {"error": "PDF_RUNTIME_UNAVAILABLE"}, 1
    except PdfError as exc:
        reply, status = {"error": str(exc)}, 1
    except Exception:
        reply, status = {"error": "PDF_TASK_FAILED"}, 1
    sys.stdout.buffer.write(json.dumps(reply, ensure_ascii=False).encode("utf-8"))
    return status


if __name__ == "__main__":
    with open(os.devnull, "w") as sink, contextlib.redirect_stderr(sink):
        raise SystemExit(main())
