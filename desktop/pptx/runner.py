"""Offline presentation reader and renderer for the desktop task."""

from __future__ import annotations

import base64
import io
import json
import shutil
import sys
import unicodedata
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

FILE_LIMIT = 20 * 1024 * 1024
EXPANDED_LIMIT = 64 * 1024 * 1024
TEXT_LIMIT = 60000
CHART_URIS = {
    "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "http://schemas.microsoft.com/office/drawing/2014/chartex",
}


class PptxError(Exception):
    pass


def deny_network(event: str, _args: tuple[object, ...]) -> None:
    if event.startswith("socket."):
        raise PptxError("PPTX_TASK_FAILED")


def slide_text(shapes: Any) -> Iterator[str]:
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from slide_text(shape.shapes)
        elif shape.has_text_frame and shape.text.strip():
            yield shape.text.strip()
        elif shape.has_table:
            for row in shape.table.rows:
                yield "\t".join(cell.text for cell in row.cells if not cell.is_spanned)


def read_deck(data: bytes) -> str:
    from pptx import Presentation
    from pptx.oxml.ns import qn

    try:
        bounded = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(data)) as package,
            zipfile.ZipFile(bounded, "w") as uncompressed,
        ):
            entries = package.infolist()
            if len(entries) > 4096 or sum(entry.file_size for entry in entries) > EXPANDED_LIMIT:
                raise PptxError("PPTX_SOURCE_LIMIT")
            if len(entries) != len({entry.filename for entry in entries}):
                raise PptxError("PPTX_INVALID_SOURCE")
            # Bound individual inflate calls before the library reads complete parts.
            for entry in entries:
                if entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise PptxError("PPTX_INVALID_SOURCE")
                with package.open(entry) as source, uncompressed.open(entry.filename, "w") as part:
                    shutil.copyfileobj(source, part, 64 * 1024)

        paragraphs = []
        size = 0
        graphic_data = qn("a:graphicData")
        for index, slide in enumerate(Presentation(bounded).slides, 1):
            # Shape enumeration skips charts in markup compatibility branches.
            if any(node.get("uri") in CHART_URIS for node in slide.element.iter(graphic_data)):
                raise PptxError("PPTX_UNSUPPORTED_CHART")
            first = True
            for text in slide_text(slide.shapes):
                if not text.strip():
                    continue
                if first:
                    heading = f"[Slide {index}]"
                    paragraphs.append(heading)
                    size += len(heading) + 1
                    first = False
                size += len(text) + 1
                if size > TEXT_LIMIT:
                    raise PptxError("PPTX_SOURCE_LIMIT")
                paragraphs.append(text)
    except PptxError:
        raise
    except Exception:
        raise PptxError("PPTX_INVALID_SOURCE") from None
    if not paragraphs:
        raise PptxError("PPTX_NO_TEXT")
    return "\n".join(paragraphs)


def report_text(value: Any, limit: int) -> str:
    if isinstance(value, str) and all(
        character.isprintable() or unicodedata.category(character) == "Zs" for character in value
    ):
        text = value.strip()
        if 1 <= len(text) <= limit:
            return text
    raise PptxError("PPTX_INVALID_REPORT")


def create_deck(data: bytes) -> bytes:
    from pptx import Presentation
    from pptx.util import Pt

    try:
        report = json.loads(data)
    except (ValueError, RecursionError):
        raise PptxError("PPTX_INVALID_REPORT") from None
    if not isinstance(report, dict) or report.keys() != {"slides"}:
        raise PptxError("PPTX_INVALID_REPORT")
    slides = report["slides"]
    if not isinstance(slides, list) or not 1 <= len(slides) <= 20:
        raise PptxError("PPTX_INVALID_REPORT")

    deck = Presentation()
    for page in slides:
        if not isinstance(page, dict) or page.keys() != {"title", "bullets"}:
            raise PptxError("PPTX_INVALID_REPORT")
        bullets = page["bullets"]
        if not isinstance(bullets, list) or not 1 <= len(bullets) <= 5:
            raise PptxError("PPTX_INVALID_REPORT")

        slide = deck.slides.add_slide(deck.slide_layouts[1])
        title = slide.shapes.title.text_frame
        title.word_wrap = True
        title.paragraphs[0].text = report_text(page["title"], 48)
        title.paragraphs[0].font.size = Pt(26)
        body = slide.placeholders[1].text_frame
        body.word_wrap = True
        for index, bullet in enumerate(bullets):
            paragraph = body.add_paragraph() if index else body.paragraphs[0]
            paragraph.text = report_text(bullet, 64)
            paragraph.font.size = Pt(18)
            paragraph.space_after = Pt(8)

    output = io.BytesIO()
    deck.save(output)
    return output.getvalue()


def main() -> int:
    sys.addaudithook(deny_network)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))
    try:
        if sys.argv[1:] == ["read"]:
            data = sys.stdin.buffer.read(FILE_LIMIT + 1)
            if len(data) > FILE_LIMIT:
                raise PptxError("PPTX_SOURCE_LIMIT")
            reply = {"text": read_deck(data)}
        elif sys.argv[1:] == ["create"]:
            limit = TEXT_LIMIT * 4
            data = sys.stdin.buffer.read(limit + 1)
            if len(data) > limit:
                raise PptxError("PPTX_INVALID_REPORT")
            reply = {"deck": base64.b64encode(create_deck(data)).decode("ascii")}
        else:
            raise PptxError("PPTX_TASK_FAILED")
        status = 0
    except ImportError:
        reply, status = {"error": "PPTX_RUNTIME_UNAVAILABLE"}, 1
    except PptxError as exc:
        reply, status = {"error": str(exc)}, 1
    except Exception:
        reply, status = {"error": "PPTX_TASK_FAILED"}, 1
    sys.stdout.buffer.write(json.dumps(reply, ensure_ascii=False).encode("utf-8"))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
