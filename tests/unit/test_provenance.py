"""숫자 출처 추적 단위 테스트 (FR-21)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from docmill.tools.provenance import (
    ProvenanceError,
    SheetTable,
    Sources,
    TextNumber,
    collect,
    column_letter,
    column_number,
    dump,
    load_chart_sources,
    provenance_path,
    range_origin,
)
from docmill.tools.report import Group
from docmill.tools.verify import compare, read_chart_points

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"

ROWS = [
    ["월", "부서", "매출"],
    ["7월", "A", 10],
    ["7월", "B", 30],
    ["8월", "A", 20],
    ["8월", None, 5],
]


def _table() -> SheetTable:
    return SheetTable(file="in.xlsx", sheet="시트", first_row=3, first_col=2, values=ROWS)


def _slide(body: str) -> str:
    paragraphs = "".join(f"<a:p><a:r><a:t>{line}</a:t></a:r></a:p>" for line in body.split("\n"))
    return (
        f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree>'
        '<p:sp><p:nvSpPr><p:cNvPr id="7" name="Title"/></p:nvSpPr>'
        "<p:txBody><a:p><a:r><a:t>요약</a:t></a:r></a:p></p:txBody></p:sp>"
        '<p:sp><p:nvSpPr><p:cNvPr id="8" name="Body"/></p:nvSpPr>'
        f"<p:txBody>{paragraphs}</p:txBody></p:sp>"
        "</p:spTree></p:cSld></p:sld>"
    )


CHART_SLIDE = (
    f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree>'
    '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="9" name="Chart"/></p:nvGraphicFramePr>'
    f'<a:graphic><a:graphicData><c:chart xmlns:c="{C}" r:id="rId2"/></a:graphicData></a:graphic>'
    "</p:graphicFrame></p:spTree></p:cSld></p:sld>"
)

CHART = (
    f'<c:chartSpace xmlns:c="{C}"><c:chart><c:plotArea><c:barChart><c:ser>'
    '<c:cat><c:strRef><c:strCache><c:pt idx="0"><c:v>A</c:v></c:pt>'
    '<c:pt idx="1"><c:v>B</c:v></c:pt></c:strCache></c:strRef></c:cat>'
    '<c:val><c:numRef><c:numCache><c:pt idx="0"><c:v>30</c:v></c:pt>'
    '<c:pt idx="1"><c:v>30</c:v></c:pt></c:numCache></c:numRef></c:val>'
    "</c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>"
)


def _rels(*targets: tuple[str, str]) -> str:
    items = "".join(f'<Relationship Id="{i}" Target="{t}"/>' for i, t in targets)
    return f'<Relationships xmlns="{REL}">{items}</Relationships>'


def _pptx(path: Path, body: str) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "ppt/presentation.xml",
            f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>'
            '<p:sldId id="256" r:id="rId8"/><p:sldId id="257" r:id="rId9"/>'
            "</p:sldIdLst></p:presentation>",
        )
        # Slide parts are deliberately listed out of numeric order in the package.
        z.writestr(
            "ppt/_rels/presentation.xml.rels",
            _rels(("rId8", "slides/slide2.xml"), ("rId9", "slides/slide1.xml")),
        )
        z.writestr("ppt/slides/slide2.xml", _slide(body))
        z.writestr("ppt/slides/slide1.xml", CHART_SLIDE)
        z.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            _rels(("rId1", "../slideLayouts/x.xml"), ("rId2", "../charts/chart3.xml")),
        )
        z.writestr("ppt/charts/chart3.xml", CHART)
    return path


def test_column_letters_round_trip() -> None:
    for number, letters in [(1, "A"), (3, "C"), (26, "Z"), (27, "AA"), (703, "AAA")]:
        assert column_letter(number) == letters
        assert column_number(letters) == number


@pytest.mark.parametrize(
    ("address", "origin"),
    [("A1:C16", (1, 1)), ("B3:D9", (3, 2)), ("'s'!$C$4", (4, 3)), ("", (1, 1))],
)
def test_range_origin(address: str, origin: tuple[int, int]) -> None:
    assert range_origin(address) == origin


def test_sources_use_sheet_offsets_and_list_excluded_rows() -> None:
    sources = Sources(_table(), "부서", "매출")
    assert sources.total() == {
        "file": "in.xlsx",
        "sheet": "시트",
        "aggregation": "sum",
        "range": "D4:D7",
        "excluded_rows": [7],
    }
    share = sources.group_share("A")
    assert share["aggregation"] == "share"
    assert (share["range"], share["criteria_range"], share["criteria"]) == ("D4:D7", "C4:C7", "A")
    assert sources.group_count()["range"] == "C4:C7"


def test_sources_record_normalization_and_display_digits() -> None:
    sources = Sources(_table(), "부서", "매출")
    group = sources.group_total("A", 2)
    assert (group["normalize"], group["digits"]) == ("strip", 2)
    assert sources.group_count()["normalize"] == "strip"
    assert sources.total(0)["digits"] == 0
    assert "digits" not in sources.total()


def test_collect_finds_shapes_and_chart_points(tmp_path: Path) -> None:
    pptx = _pptx(tmp_path / "r.pptx", "합계: 60\n부서 수: 2개")
    sources = Sources(_table(), "부서", "매출")
    body = "합계: 60\n부서 수: 2개"
    texts = {
        0: [
            TextNumber("summary", body, 60.0, sources.total()),
            TextNumber("summary", body, 2.0, sources.group_count()),
        ]
    }
    numbers = collect(pptx, ["summary", "chart"], texts, sources, ["A", "B"])
    assert [(n["value"], n["location"]) for n in numbers] == [
        (60, {"slide": 2, "shape": "8"}),
        (2, {"slide": 2, "shape": "8"}),
        (30, {"slide": 1, "shape": "9", "chart": "chart3", "series": 1, "point": 0}),
        (30, {"slide": 1, "shape": "9", "chart": "chart3", "series": 1, "point": 1}),
    ]
    assert [n["source"].get("criteria") for n in numbers[2:]] == ["A", "B"]
    assert {n["kind"] for n in numbers} == {"summary", "chart"}


def test_collect_fails_when_number_is_not_in_document(tmp_path: Path) -> None:
    pptx = _pptx(tmp_path / "r.pptx", "다른 내용")
    sources = Sources(_table(), "부서", "매출")
    texts = {0: [TextNumber("summary", "합계: 60", 60.0, sources.total())]}
    with pytest.raises(ProvenanceError, match="도형"):
        collect(pptx, ["summary", "chart"], texts, sources, ["A", "B"])


def test_collect_fails_on_slide_count_mismatch(tmp_path: Path) -> None:
    pptx = _pptx(tmp_path / "r.pptx", "x")
    with pytest.raises(ProvenanceError, match="슬라이드 수"):
        collect(pptx, ["chart"], {}, Sources(_table(), "부서", "매출"), ["A", "B"])


def test_dump_is_deterministic_and_loads_chart_sources(tmp_path: Path) -> None:
    pptx = _pptx(tmp_path / "r.pptx", "x")
    sources = Sources(_table(), "부서", "매출")
    numbers = collect(pptx, ["summary", "chart"], {}, sources, ["A", "B"])
    data = dump(numbers)
    assert data == dump(json.loads(data)["numbers"])
    provenance_path(pptx).write_bytes(data)
    loaded = load_chart_sources(pptx)
    assert loaded is not None
    assert loaded[("chart3", 1, 1)] == sources.group_total("B")


def test_missing_provenance_file_returns_none(tmp_path: Path) -> None:
    assert load_chart_sources(tmp_path / "none.pptx") is None


def test_compare_attaches_source_only_to_mismatches(tmp_path: Path) -> None:
    pptx = _pptx(tmp_path / "r.pptx", "x")
    sources = Sources(_table(), "부서", "매출")
    found = {("chart3", 1, 0): sources.group_total("A"), ("chart3", 1, 1): {"x": 1}}
    groups = [Group("A", 30.0, 50.0), Group("B", 31.0, 50.0)]
    result = compare(read_chart_points(pptx), groups, found)
    assert result["matched"] == 1
    (bad,) = result["mismatches"]
    assert bad["source"] == {"x": 1}
    assert "source" not in compare(read_chart_points(pptx), groups)["mismatches"][0]


def test_verify_numbers_reports_unreadable_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docmill.tools import verify

    monkeypatch.setattr(verify, "_read_sheet", lambda *_: [])
    monkeypatch.setattr(verify, "aggregate", lambda *_: [Group("A", 30.0, 50.0)])
    xlsx = tmp_path / "s.xlsx"
    xlsx.write_bytes(b"")
    pptx = _pptx(tmp_path / "r.pptx", "x")
    provenance_path(pptx).write_text("{broken", encoding="utf-8")
    result = json.loads(verify.verify_numbers(str(pptx), str(xlsx), "부서", "매출"))
    assert "출처 파일" in result["provenance_error"]
    assert result["mismatches"] and "source" not in result["mismatches"][0]
