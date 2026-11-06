"""Isolated, offline PDF OCR worker for the native desktop client."""

from __future__ import annotations

import contextlib
import json
import math
import sys
from pathlib import Path

FILE_LIMIT = 20 * 1024 * 1024
TEXT_LIMIT = 60000
PAGE_LIMIT = 20
PIXEL_LIMIT = 16_000_000
MODELS = {
    "Det": "PP-OCRv6_det_small.onnx",
    "Cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "Rec": "PP-OCRv6_rec_small.onnx",
}


class OcrError(Exception):
    pass


def deny_network(event: str, args: tuple[object, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.sendmsg"}:
        raise OcrError("OCR_NETWORK_DENIED")


def extract(data: bytes, models: Path) -> str:
    if len(data) > FILE_LIMIT:
        raise OcrError("OCR_FILE_LIMIT")
    if not data.startswith(b"%PDF-"):
        raise OcrError("OCR_INVALID_PDF")
    for name in MODELS.values():
        if not (models / name).is_file():
            raise OcrError("OCR_MODELS_MISSING")
    try:
        import pypdfium2 as pdfium
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise OcrError("OCR_DEPS_MISSING") from exc

    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:
        raise OcrError("OCR_PARSE_FAILED") from exc
    try:
        if not 1 <= len(document) <= PAGE_LIMIT:
            raise OcrError("OCR_PAGE_LIMIT")
        params = {f"{kind}.model_path": str(models / name) for kind, name in MODELS.items()}
        params.update(
            {
                "Global.log_level": "error",
                "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            }
        )
        engine = RapidOCR(params=params)
        pages: list[str] = []
        for index in range(len(document)):
            page = document[index]
            try:
                width, height = page.get_size()
                if (
                    not math.isfinite(width * height)
                    or width <= 0
                    or height <= 0
                    or math.ceil(width * 2.5) * math.ceil(height * 2.5) > PIXEL_LIMIT
                ):
                    raise OcrError("OCR_PAGE_SIZE")
                bitmap = page.render(scale=2.5)
                try:
                    result = engine(bitmap.to_pil())
                finally:
                    bitmap.close()
                text = "\n".join(result.txts or ()).strip()
                if not text:
                    raise OcrError("OCR_PAGE_EMPTY")
                pages.append(f"--- 第 {index + 1} 页 ---\n{text}\n")
                if sum(map(len, pages)) + len(pages) - 1 > TEXT_LIMIT:
                    raise OcrError("OCR_TEXT_LIMIT")
            finally:
                page.close()
        return "\n".join(pages)
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError("OCR_RECOGNITION_FAILED") from exc
    finally:
        document.close()


def main() -> int:
    sys.addaudithook(deny_network)
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root.parent / "packages"))
    try:
        if len(sys.argv) != 1:
            raise OcrError("OCR_INVALID_ARGS")
        data = sys.stdin.buffer.read(FILE_LIMIT + 1)
        with contextlib.redirect_stdout(sys.stderr):
            text = extract(data, root / "models")
        reply, status = {"text": text}, 0
    except OcrError as exc:
        reply, status = {"error": str(exc)}, 1
    except Exception:
        reply, status = {"error": "OCR_INTERNAL"}, 1
    sys.stdout.buffer.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
