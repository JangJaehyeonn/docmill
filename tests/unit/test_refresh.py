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


def test_negative_totals_round_trip_over_two_refreshes(tmp_path: Path) -> None:
    source, doc = make_doc(tmp_path)
    write_xlsx(source, [("가팀", -60), ("나팀", 300), ("가팀", -40), (None, None)])
    first = _run(doc)
    assert "-100" in _part(doc, "ppt/slides/slide2.xml")
    assert first["changes"]
    write_xlsx(source, [("가팀", 120), ("나팀", 300), ("가팀", 80), (None, None)])
    second = _run(doc)
    assert "error" not in second, second
    assert "가팀: 200 (" in _part(doc, "ppt/slides/slide2.xml")


def test_phonetic_guide_is_not_part_of_cell_text(tmp_path: Path) -> None:
    path = tmp_path / "s.xlsx"
    write_xlsx(path, [("営業", 5)])
    with zipfile.ZipFile(path) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    sheet = parts["xl/worksheets/sheet2.xml"].decode("utf-8")
    sheet = sheet.replace(
        "<is><t>営業</t></is>", '<is><t>営業</t><rPh sb="0" eb="2"><t>えいぎょう</t></rPh></is>'
    )
    parts["xl/worksheets/sheet2.xml"] = sheet.encode("utf-8")
    with zipfile.ZipFile(path, "w") as z:
        for name, data in parts.items():
            z.writestr(name, data)
    assert read_cells(path, "매출")["A2"] == "営業"


def test_name_digits_are_not_taken_for_the_total(tmp_path: Path) -> None:
    source = tmp_path / "sales.xlsx"
    write_xlsx(source, [("2024 사업부", 2024), ("나팀", 6000)])
    doc = tmp_path / "report.pptx"
    write_pptx(doc, ["2024 사업부: 2,024 (25.0%)"])
    numbers = [
        _text(2024, _src(source, "sumif", criteria="2024 사업부", digits=0)),
        _text(25.0, _src(source, "share", criteria="2024 사업부")),
    ]
    provenance_path(doc).write_bytes(dump(numbers))
    write_xlsx(source, [("2024 사업부", 2025), ("나팀", 6000)])
    out = _run(doc)
    assert "error" not in out, out
    assert "2024 사업부: 2,025 (" in _part(doc, "ppt/slides/slide2.xml")


def test_small_displayed_change_on_large_total_is_a_change(tmp_path: Path) -> None:
    source = tmp_path / "sales.xlsx"
    write_xlsx(source, [("가팀", 1_000_000_000)])
    doc = tmp_path / "report.pptx"
    write_pptx(doc, ["매출 합계: 1,000,000,000"])
    provenance_path(doc).write_bytes(dump([_text(1_000_000_000, _src(source, "sum", digits=0))]))
    write_xlsx(source, [("가팀", 1_000_000_001)])
    out = _run(doc)
    assert len(out["changes"]) == 1
    assert "1,000,000,001" in _part(doc, "ppt/slides/slide2.xml")


def _numbers(doc: Path) -> list[dict[str, Any]]:
    return list(json.loads(provenance_path(doc).read_text())["numbers"])


def _write_runs(path: Path, runs: list[str]) -> None:
    """한 문단을 여러 런(`a:r`)으로 나눈 슬라이드를 쓴다."""
    body = "<a:p>" + "".join(f"<a:r><a:rPr/><a:t>{r}</a:t></a:r>" for r in runs) + "</a:p>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml", "<p:presentation/>")
        z.writestr("ppt/slides/slide2.xml", _SLIDE.format(body=body))
        z.writestr("ppt/charts/chart1.xml", _CHART.format(a="100", b="300"))


def test_name_split_over_runs_is_not_taken_for_the_total(tmp_path: Path) -> None:
    source = tmp_path / "sales.xlsx"
    write_xlsx(source, [("2024 사업부", 2024), ("나팀", 6072)])
    doc = tmp_path / "report.pptx"
    _write_runs(doc, ["20", "24 사업부: ", "2,024 (25.0%)"])
    numbers = [
        _text(2024, _src(source, "sumif", criteria="2024 사업부", digits=0)),
        _text(25.0, _src(source, "share", criteria="2024 사업부")),
    ]
    provenance_path(doc).write_bytes(dump(numbers))
    write_xlsx(source, [("2024 사업부", 2025), ("나팀", 6072)])
    out = _run(doc)
    assert "error" not in out, out
    slide = _part(doc, "ppt/slides/slide2.xml")
    assert "<a:t>20</a:t>" in slide and "<a:t>24 사업부: </a:t>" in slide  # 항목명 그대로
    assert "<a:t>2,025 (25.0%)</a:t>" in slide  # 합계만 바뀜
    assert [(c["old"], c["new"]) for c in out["changes"]] == [(2024, 2025)]
    assert [n["value"] for n in _numbers(doc)] == [2025, 25.0]


def test_numeric_name_equal_to_total_keeps_the_total(tmp_path: Path) -> None:
    source = tmp_path / "sales.xlsx"
    write_xlsx(source, [("100", 100), ("나팀", 300)])
    doc = tmp_path / "report.pptx"
    write_pptx(doc, ["최대: 나팀 300 (75.0%)", "100: 100 (25.0%)"])
    numbers = [
        _text(300, _src(source, "sumif", criteria="나팀", digits=0)),
        _text(75.0, _src(source, "share", criteria="나팀")),
        _text(100, _src(source, "sumif", criteria="100", digits=0), "detail"),
        _text(25.0, _src(source, "share", criteria="100"), "detail"),
    ]
    provenance_path(doc).write_bytes(dump(numbers))
    write_xlsx(source, [("100", 120), ("나팀", 300)])
    out = _run(doc)
    assert "error" not in out, out
    slide = _part(doc, "ppt/slides/slide2.xml")
    assert "<a:t>100: 120 (28.6%)</a:t>" in slide
    assert "<a:t>최대: 나팀 300 (71.4%)</a:t>" in slide
    assert [n["value"] for n in _numbers(doc)] == [300, 71.4, 120, 28.6]


def test_tiny_chart_change_is_a_change(tmp_path: Path) -> None:
    source = tmp_path / "sales.xlsx"
    write_xlsx(source, [("가팀", 0), ("나팀", 300)])
    doc = tmp_path / "report.pptx"
    write_pptx(doc, ["없음"], a="0")
    provenance_path(doc).write_bytes(dump([_point(0, 0, _src(source, "sumif", criteria="가팀"))]))
    write_xlsx(source, [("가팀", 5e-10), ("나팀", 300)])
    out = _run(doc)
    assert len(out["changes"]) == 1
    assert '<c:pt idx="0"><c:v>5e-10</c:v>' in _part(doc, "ppt/charts/chart1.xml")
    assert _run(doc)["changes"] == []


def test_failed_sidecar_write_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import docmill.tools.refresh as refresh

    source, doc = make_doc(tmp_path)
    before, prov = doc.read_bytes(), provenance_path(doc).read_bytes()
    write_xlsx(source, [("가팀", 70), ("나팀", 300), ("가팀", 40), (None, None)])
    real = refresh._stage

    def full_disk(data: bytes, final: Path) -> Path:
        if final.name.endswith(".provenance.json"):
            raise OSError("disk full")
        return real(data, final)

    monkeypatch.setattr(refresh, "_stage", full_disk)
    out = _run(doc)
    assert out["step"] == "unexpected"
    assert doc.read_bytes() == before
    assert provenance_path(doc).read_bytes() == prov
    assert not list(tmp_path.glob(".docmill-*.part"))


def test_failed_sidecar_replace_restores_document_by_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import docmill.tools.refresh as refresh

    source, doc = make_doc(tmp_path)
    before, prov = doc.read_bytes(), provenance_path(doc).read_bytes()
    write_xlsx(source, [("가팀", 70), ("나팀", 300), ("가팀", 40), (None, None)])
    real = refresh.os.replace

    def flaky(src: Any, dst: Any) -> None:
        if str(dst).endswith(".provenance.json"):
            raise OSError("rename failed")
        real(src, dst)

    monkeypatch.setattr(refresh.os, "replace", flaky)
    out = _run(doc)
    assert out["step"] == "unexpected"
    assert doc.read_bytes() == before
    assert provenance_path(doc).read_bytes() == prov
    assert not list(tmp_path.glob(".docmill-*.part"))


def test_workspace_boundary_blocks_outside_source_and_provenance(tmp_path: Path) -> None:
    from docmill.tools.refresh import refresh_links_within

    outside, root = tmp_path / "outside", tmp_path / "ws"
    outside.mkdir()
    root.mkdir()
    source, doc = make_doc(outside)
    inner = root / "report.pptx"
    inner.write_bytes(doc.read_bytes())
    provenance_path(inner).write_bytes(provenance_path(doc).read_bytes())  # 원본이 밖에 있다
    out = json.loads(refresh_links_within(str(inner), str(root)))
    assert out["step"] == "source"
    provenance_path(inner).unlink()
    (root / "sales.xlsx").write_bytes(source.read_bytes())
    numbers = [{**n, "source": {**n["source"], "file": "sales.xlsx"}} for n in _numbers(doc)]
    provenance_path(doc).write_bytes(dump(numbers))
    provenance_path(inner).symlink_to(provenance_path(doc))  # 출처 파일이 밖을 가리키는 링크
    out = json.loads(refresh_links_within(str(inner), str(root)))
    assert out["step"] == "provenance"
    provenance_path(inner).unlink()
    provenance_path(inner).write_bytes(dump(numbers))
    out = json.loads(refresh_links_within(str(inner), str(root)))
    assert "error" not in out, out


def test_same_named_sources_in_two_folders_are_not_guessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    team = tmp_path / "team"
    team.mkdir()
    _, doc = make_doc(team)
    write_xlsx(tmp_path / "sales.xlsx", [("가팀", 1), ("나팀", 2)])
    numbers = [{**n, "source": {**n["source"], "file": "sales.xlsx"}} for n in _numbers(doc)]
    provenance_path(doc).write_bytes(dump(numbers))
    monkeypatch.chdir(tmp_path)
    out = _run(doc)
    assert out["step"] == "source"
    assert "연결을 다시" in out["error"]
    monkeypatch.chdir(team)
    assert "error" not in _run(doc)


def test_lock_wait_is_bounded(tmp_path: Path) -> None:
    import docmill.tools.refresh as refresh

    _, doc = make_doc(tmp_path)
    with (
        refresh._document_lock(doc),
        pytest.raises(refresh.RefreshError) as err,
        refresh._document_lock(doc, timeout=0.2),
    ):
        pass
    assert err.value.step == "lock"
    with refresh._document_lock(doc, timeout=0.2):
        pass


def test_provenance_time_marks_the_source_version_read(tmp_path: Path) -> None:
    import os

    source, doc = make_doc(tmp_path)
    write_xlsx(source, [("가팀", 70), ("나팀", 300), ("가팀", 40), (None, None)])
    os.utime(source, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    _run(doc)
    assert provenance_path(doc).stat().st_mtime_ns == source.stat().st_mtime_ns


def test_future_dated_source_does_not_hide_a_later_save_of_another(tmp_path: Path) -> None:
    import os
    import shutil
    import time

    a, doc = make_doc(tmp_path)
    b = tmp_path / "other.xlsx"
    shutil.copy(a, b)
    numbers = [
        {**n, "source": {**n["source"], "file": str(b)}} if n["kind"] == "chart" else n
        for n in _numbers(doc)
    ]
    provenance_path(doc).write_bytes(dump(numbers))
    day = 86_400 * 1_000_000_000
    os.utime(a, ns=(time.time_ns() - day, time.time_ns() - day))
    os.utime(b, ns=(time.time_ns() + day, time.time_ns() + day))  # 시계가 어긋난 미래 시각
    before, prov = doc.read_bytes(), provenance_path(doc).read_bytes()
    assert _run(doc)["changes"] == []
    assert doc.read_bytes() == before  # 변경이 없으면 문서·출처 내용은 그대로
    assert provenance_path(doc).read_bytes() == prov
    marked = provenance_path(doc).stat().st_mtime_ns
    assert marked < b.stat().st_mtime_ns
    a.write_bytes(a.read_bytes())  # 원본 A를 다시 저장한다
    assert a.stat().st_mtime_ns > marked  # studio linkStatus가 A의 저장을 알아챈다


def test_workspace_is_the_base_for_relative_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docmill.tools.refresh import refresh_links_within

    root, team, elsewhere = tmp_path / "ws", tmp_path / "ws" / "team", tmp_path / "elsewhere"
    team.mkdir(parents=True)
    elsewhere.mkdir()
    source, doc = make_doc(team)
    source.rename(root / "sales.xlsx")
    numbers = [{**n, "source": {**n["source"], "file": "sales.xlsx"}} for n in _numbers(doc)]
    provenance_path(doc).write_bytes(dump(numbers))
    monkeypatch.chdir(elsewhere)  # 현재 폴더가 아니라 작업 폴더 기준으로 찾는다
    out = json.loads(refresh_links_within(str(doc), str(root)))
    assert "error" not in out, out
    write_xlsx(team / "sales.xlsx", [("가팀", 1), ("나팀", 2)])  # 문서 폴더에도 다른 원본
    out = json.loads(refresh_links_within(str(doc), str(root)))
    assert out["step"] == "source"
    assert "연결을 다시" in out["error"]


def test_refresh_keeps_sentence_provenance_and_follows_new_numbers(tmp_path: Path) -> None:
    source, doc = make_doc(tmp_path)
    numbers = json.loads(provenance_path(doc).read_text(encoding="utf-8"))["numbers"]
    sentences = [
        {"text": line, "location": {"slide": 2, "shape": "5"}, "source": {"file": str(source)}}
        for line in LINES
    ]
    provenance_path(doc).write_bytes(dump(numbers, sentences))
    write_xlsx(source, [("가팀", 60), ("나팀", 300), ("가팀", 140), (None, None)])
    assert _run(doc)["changes"]
    kept = json.loads(provenance_path(doc).read_text(encoding="utf-8"))["sentences"]
    assert [s["text"] for s in kept] == [
        "매출 합계: 500",
        "부서 수: 2개",
        "최대: 나팀 300 (60.0%)",
        "가팀: 200 (40.0%)",
    ]
    assert all(s["source"] == {"file": str(source)} for s in kept)
