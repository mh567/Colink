"""Verify the offline worker with real presentations and its shipped process protocol."""

import base64
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

RUNNER = Path(__file__).resolve().parents[2] / "desktop" / "pptx" / "runner.py"


def run_worker(mode, data):
    result = subprocess.run(
        [sys.executable, "-I", str(RUNNER), mode],
        input=data,
        capture_output=True,
        timeout=15,
    )
    assert result.stdout, result.stderr.decode(errors="replace")
    return result.returncode, json.loads(result.stdout)


def test_worker_reads_visible_slide_text_and_table_cells():
    source = io.BytesIO()
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = "项目进展"
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(8), Inches(2)).table
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "完成数"
    table.cell(1, 0).text = "现场验收"
    table.cell(1, 1).text = "12"
    deck.save(source)

    code, result = run_worker("read", source.getvalue())

    assert code == 0
    assert "项目进展" in result["text"]
    assert "现场验收\t12" in result["text"]


@pytest.mark.parametrize("extended_chart", [False, True])
def test_worker_rejects_chart_instead_of_returning_only_the_slide_title(extended_chart):
    source = io.BytesIO()
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = "季度完成情况"
    data = CategoryChartData()
    data.categories = ["一季度", "二季度"]
    data.add_series("完成数", [12, 18])
    frame = slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(2), Inches(6), Inches(4), data
    )
    if extended_chart:
        frame.element.graphic.graphicData.uri = (
            "http://schemas.microsoft.com/office/drawing/2014/chartex"
        )
    deck.save(source)

    code, result = run_worker("read", source.getvalue())

    assert code != 0
    assert result == {"error": "PPTX_UNSUPPORTED_CHART"}


def test_worker_creates_native_editable_shapes():
    code, result = run_worker(
        "create",
        '{"slides":[{"title":"项目汇报","bullets":["已完成 12 项","本周验收"]}]}'.encode(),
    )
    assert code == 0
    deck = Presentation(io.BytesIO(base64.b64decode(result["deck"], validate=True)))
    slide = deck.slides[0]
    assert slide.shapes.title.text == "项目汇报"
    assert slide.placeholders[1].text == "已完成 12 项\n本周验收"
    slide.shapes.title.text = "已核对"
    edited = io.BytesIO()
    deck.save(edited)
    assert Presentation(edited).slides[0].shapes.title.text == "已核对"


@pytest.mark.parametrize("space", ["\u00a0", "\u202f", "\u3000"])
def test_worker_preserves_unicode_spaces_within_report_text(space):
    bullet = f"预计投入 12{space}345 元"
    report = {"slides": [{"title": "项目汇报", "bullets": [bullet]}]}

    code, result = run_worker("create", json.dumps(report).encode())

    assert code == 0
    deck = Presentation(io.BytesIO(base64.b64decode(result["deck"], validate=True)))
    assert deck.slides[0].placeholders[1].text == bullet


@pytest.mark.parametrize(
    ("mode", "data", "error"),
    [
        ("read", b"not a slide deck", "PPTX_INVALID_SOURCE"),
        ("create", b'{"slides":[]}', "PPTX_INVALID_REPORT"),
        (
            "create",
            b'{"slides":[{"title":"a","bullets":["b\\nc"]}]}',
            "PPTX_INVALID_REPORT",
        ),
        (
            "create",
            b'{"slides":[{"title":"a","bullets":["b\\t"]}]}',
            "PPTX_INVALID_REPORT",
        ),
        (
            "create",
            b'{"slides":[{"title":"a","bullets":["b\\u0000c"]}]}',
            "PPTX_INVALID_REPORT",
        ),
        (
            "create",
            b'{"slides":[{"title":"a","bullets":["b"],"image":"/private/logo.png"}]}',
            "PPTX_INVALID_REPORT",
        ),
    ],
)
def test_worker_rejects_corrupt_inputs_and_unsafe_model_fields(mode, data, error):
    code, result = run_worker(mode, data)
    assert code != 0
    assert result == {"error": error}


def test_worker_rejects_nonstandard_archive_compression():
    source = io.BytesIO()
    deck = Presentation()
    deck.slides.add_slide(deck.slide_layouts[5]).shapes.title.text = "项目进展"
    deck.save(source)
    repacked = io.BytesIO()
    with (
        zipfile.ZipFile(source) as original,
        zipfile.ZipFile(repacked, "w", compression=zipfile.ZIP_BZIP2) as result,
    ):
        for name in original.namelist():
            result.writestr(name, original.read(name))

    code, result = run_worker("read", repacked.getvalue())

    assert code != 0
    assert result == {"error": "PPTX_INVALID_SOURCE"}
