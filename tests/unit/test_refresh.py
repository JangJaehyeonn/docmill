"""refresh_links (FR-37) 단위 테스트. 엔진 없이 zipfile로 만든 xlsx·pptx를 쓴다."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from docmill.cli import app
from docmill.tools.provenance import dump, provenance_path
from docmill.tools.refresh import aggregate, read_cells, refresh_links

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"

Row = tuple[str | None, float | str | None]


def _cell(ref: str, value: float | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
    return f'<c r="{ref}" t="n"><v>{value}</v></c>'


def write_xlsx(path: Path, rows: list[Row], shared_key: str | None = None) -> None:
    """A열 부서·B열 매출 표를 '매출' 시트에 쓴다. shared_key가 있으면 A2를 공유 문자열로 쓴다."""
    body = '<row r="1">' + _cell("A1", "부서") + _cell("B1", "매출") + "</row>"
    for n, (key, value) in enumerate(rows, start=2):
        a = _cell(f"A{n}", key)
        if n == 2 and shared_key is not None:
            a = '<c r="A2" t="s"><v>0</v></c>'
        body += f'<row r="{n}">{a}{_cell(f"B{n}", value)}</row>'
    sheet = f'<worksheet xmlns="{_MAIN}"><sheetData>{body}</sheetData></worksheet>'
    workbook = (
        f'<workbook xmlns="{_MAIN}" xmlns:r="{_REL}"><sheets>'
        '<sheet name="요약" sheetId="1" r:id="rId1"/><sheet name="매출" sheetId="2" r:id="rId2"/>'
        "</sheets></workbook>"
    )
    rels = (
        f'<Relationships xmlns="{_PKG}">'
        '<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="x" Target="worksheets/sheet2.xml"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr(
            "xl/worksheets/sheet1.xml", f'<worksheet xmlns="{_MAIN}"><sheetData/></worksheet>'
        )
        z.writestr("xl/worksheets/sheet2.xml", sheet)
        if shared_key is not None:
            z.writestr(
                "xl/sharedStrings.xml", f'<sst xmlns="{_MAIN}"><si><t>{shared_key}</t></si></sst>'
            )


_SLIDE = (
    '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree>'
    '<p:sp><p:nvSpPr><p:cNvPr id="5" name="t"/></p:nvSpPr><p:txBody>{body}</p:txBody></p:sp>'
    "</p:spTree></p:cSld></p:sld>"
)
_CHART = (
    '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart>'
    '<c:ser><c:cat><c:strCache><c:pt idx="0"><c:v>가팀</c:v></c:pt><c:pt idx="1"><c:v>나팀</c:v>'
    "</c:pt></c:strCache></c:cat><c:val><c:numCache>"
    '<c:pt idx="0"><c:v>{a}</c:v></c:pt><c:pt idx="1"><c:v>{b}</c:v></c:pt>'
    "</c:numCache></c:val></c:ser></c:chart></c:chartSpace>"
)


def _para(text: str) -> str:
    return f"<a:p><a:r><a:rPr/><a:t>{text}</a:t></a:r></a:p>"


def write_pptx(path: Path, lines: list[str], a: str = "100", b: str = "300") -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml", "<p:presentation/>")
        z.writestr("ppt/slides/slide2.xml", _SLIDE.format(body="".join(map(_para, lines))))
        z.writestr("ppt/charts/chart1.xml", _CHART.format(a=a, b=b))


def _src(source: Path, aggregation: str, **extra: Any) -> dict[str, Any]:
    src: dict[str, Any] = {"file": str(source), "sheet": "매출", "aggregation": aggregation}
    src["range"] = "A2:A5" if aggregation == "count_distinct" else "B2:B5"
    if "criteria" in extra:
        src["criteria_range"] = "A2:A5"
    return {**src, **extra}


def _text(value: float, source: dict[str, Any], kind: str = "summary") -> dict[str, Any]:
    return {"value": value, "kind": kind, "location": {"slide": 2, "shape": "5"}, "source": source}


def _point(value: float, point: int, source: dict[str, Any]) -> dict[str, Any]:
    loc = {"slide": 2, "shape": "9", "chart": "chart1", "series": 1, "point": point}
    return {"value": value, "kind": "chart", "location": loc, "source": source}


ROWS: list[Row] = [("가팀", 60), ("나팀", 300), ("가팀", 40), (None, None)]
LINES = ["매출 합계: 400", "부서 수: 2개", "최대: 나팀 300 (75.0%)", "가팀: 100 (25.0%)"]


def make_doc(tmp_path: Path, rows: list[Row] = ROWS) -> tuple[Path, Path]:
    source = tmp_path / "sales.xlsx"
    write_xlsx(source, rows)
    doc = tmp_path / "report.pptx"
    write_pptx(doc, LINES)
    numbers = [
        _text(400, _src(source, "sum", digits=0)),
        _text(2, _src(source, "count_distinct", normalize="strip")),
        _text(300, _src(source, "sumif", criteria="나팀", digits=0)),
        _text(75.0, _src(source, "share", criteria="나팀")),
        _text(100, _src(source, "sumif", criteria="가팀", digits=0), "detail"),
        _text(25.0, _src(source, "share", criteria="가팀"), "detail"),
        _point(100, 0, _src(source, "sumif", criteria="가팀")),
        _point(300, 1, _src(source, "sumif", criteria="나팀")),
    ]
    provenance_path(doc).write_bytes(dump(numbers))
    return source, doc


def _run(doc: Path) -> dict[str, Any]:
    return dict(json.loads(refresh_links(str(doc))))


def _part(doc: Path, name: str) -> str:
    with zipfile.ZipFile(doc) as z:
        return z.read(name).decode("utf-8")


def test_unchanged_source_writes_nothing(tmp_path: Path) -> None:
    _, doc = make_doc(tmp_path)
    before, prov = doc.read_bytes(), provenance_path(doc).read_bytes()
    out = _run(doc)
    assert out["changes"] == []
    assert out["warnings"] == []
    assert doc.read_bytes() == before
    assert provenance_path(doc).read_bytes() == prov


def test_changed_source_fixes_chart_and_sentences(tmp_path: Path) -> None:
    source, doc = make_doc(tmp_path)
    write_xlsx(source, [("가팀", 60), ("나팀", 300), ("가팀", 140), (None, None)])
    out = _run(doc)
    pairs = sorted((c["old"], c["new"]) for c in out["changes"])
    # 합계 400→500, 나팀 비중 75→60, 가팀 100→200(본문·그래프), 가팀 비중 25→40
    assert pairs == [(25.0, 40.0), (75.0, 60.0), (100, 200), (100, 200), (400, 500)]
    assert all(c["label"] and c["location"] for c in out["changes"])
    slide = _part(doc, "ppt/slides/slide2.xml")
    for line in ("매출 합계: 500", "부서 수: 2개", "최대: 나팀 300 (60.0%)", "가팀: 200 (40.0%)"):
        assert f"<a:t>{line}</a:t>" in slide
    assert '<c:pt idx="0"><c:v>200</c:v></c:pt><c:pt idx="1"><c:v>300</c:v>' in _part(
        doc, "ppt/charts/chart1.xml"
    )
    assert _part(doc, "ppt/presentation.xml") == "<p:presentation/>"
    values = [n["value"] for n in json.loads(provenance_path(doc).read_text())["numbers"]]
    assert values == [500, 2, 300, 60.0, 200, 40.0, 200, 300]
    assert _run(doc)["changes"] == []  # 고친 뒤 다시 돌리면 바뀔 것이 없다


def test_thousands_and_decimals_use_report_format(tmp_path: Path) -> None:
    source, doc = make_doc(tmp_path)
    write_xlsx(source, [("가팀", 1000.5), ("나팀", 300), ("가팀", 40), (None, None)])
    _run(doc)
    slide = _part(doc, "ppt/slides/slide2.xml")
    assert "매출 합계: 1,340.50" in slide
    assert "가팀: 1,040.50 (77.6%)" in slide
    numbers = json.loads(provenance_path(doc).read_text())["numbers"]
    assert numbers[0]["source"]["digits"] == 2
    assert numbers[6]["value"] == 1040.5
    assert "<c:v>1040.5</c:v>" in _part(doc, "ppt/charts/chart1.xml")


def test_warns_when_top_group_or_groups_change(tmp_path: Path) -> None:
    source, doc = make_doc(tmp_path)
    write_xlsx(source, [("가팀", 600), ("나팀", 300), ("다팀", 40), (None, None)])
    out = _run(doc)
    assert any("다팀" in w and "문서에 없는" in w for w in out["warnings"])
    assert any("최대 항목은 이제 가팀" in w for w in out["warnings"])


def test_excluded_rows_are_recomputed(tmp_path: Path) -> None:
    source, doc = make_doc(tmp_path)
    write_xlsx(source, [("가팀", 60), ("나팀", 300), ("가팀", 40), (None, 7)])
    out = _run(doc)
    assert out["changes"] == []
    numbers = json.loads(provenance_path(doc).read_text())["numbers"]
    assert all(n["source"]["excluded_rows"] == [5] for n in numbers)


def test_locate_failure_leaves_files_untouched(tmp_path: Path) -> None:
    source, doc = make_doc(tmp_path)
    write_pptx(doc, ["매출 합계: 없음"])
    before, prov = doc.read_bytes(), provenance_path(doc).read_bytes()
    write_xlsx(source, [("가팀", 61), ("나팀", 300), ("가팀", 40), (None, None)])
    out = _run(doc)
    assert out["step"] == "locate"
    assert doc.read_bytes() == before
    assert provenance_path(doc).read_bytes() == prov


@pytest.mark.parametrize(
    ("setup", "step"),
    [("no_provenance", "provenance"), ("no_source", "source"), ("bad_json", "provenance")],
)
def test_failures_return_step(tmp_path: Path, setup: str, step: str) -> None:
    source, doc = make_doc(tmp_path)
    if setup == "no_provenance":
        provenance_path(doc).unlink()
    elif setup == "no_source":
        source.unlink()
    else:
        provenance_path(doc).write_text('{"numbers": [{"value": 1}]}', encoding="utf-8")
    out = _run(doc)
    assert out["step"] == step
    assert out["error"]


def test_rejects_non_pptx(tmp_path: Path) -> None:
    assert _run(tmp_path / "a.docx")["step"] == "input"


def test_read_cells_and_aggregate_follow_report_rules(tmp_path: Path) -> None:
    path = tmp_path / "s.xlsx"
    write_xlsx(
        path, [(" 가팀 ", "1,000"), ("나팀", 5), ("다팀", None), (None, 9)], shared_key="가팀 "
    )
    cells = read_cells(path, "매출")
    assert cells["A2"] == "가팀 "
    table = aggregate(cells, "B2:B5", "A2:A5")
    assert table.sums == {"가팀": 1000.0, "나팀": 5.0}
    assert table.total == 1005.0
    assert table.excluded == [4, 5]


def test_cli_refresh_links_prints_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path / "home"))
    _, doc = make_doc(tmp_path)
    result = CliRunner().invoke(app, ["refresh-links", str(doc)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["changes"] == []
