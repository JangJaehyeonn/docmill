"""FR-21 숫자 출처 추적 인수 테스트.

필요한 샘플:
- tests/fixtures/sales_q3.xlsx (있음): 시트 하나, 머리글 `부서·월·매출`(A·B·C열), 15행.
  부서는 월마다 번갈아 나온다 (영업1팀은 2·7·12행). 그래서 부서별 합계는 조건부 합계로 표현한다.
가정(구현 방법이 정해지면 맞춰 고친다):
- 출처 파일 경로는 결과 pptx 경로 + `.provenance.json` 이다 (예: report.pptx.provenance.json).
- 도구 결과에 `provenance`(파일 경로)가 들어 있다.
- provenance.json은 `{"numbers": [...]}` 이고 각 항목은 다음을 가진다.
  - `value`: 문서에 보이는 숫자 그대로 (예: 4050, 39.9, 5). 비중은 문서처럼 소수 첫째 자리.
  - `kind`: `summary` · `chart` · `detail`
  - `location`: `slide`(1부터, 슬라이드 파트 slideN.xml의 N). 그리고
    - 본문 숫자: `shape` = 그 숫자가 든 도형(또는 표 프레임)의 `p:cNvPr` `id`.
      표라면 `table_cell`을 덧붙일 수 있다.
    - 그래프 숫자: `chart`(그래프 파트 이름, 예: chart1)·`series`(1부터)·`point`(0부터 c:pt idx).
      그 슬라이드가 해당 그래프 파트를 참조해야 한다.
  - `source`: `file`·`sheet`·`aggregation`·`range`, 집계에 따라 `criteria_range`·`criteria`.
    `range`·`criteria_range`는 `C2:C16`처럼 열 하나의 연속 범위이고 길이가 같다.
- 집계 방식:
  - `sum`: range의 숫자 합
  - `sumif`: criteria_range 값이 criteria와 같은 행의 range 값 합 (부서별 합계)
  - `count_distinct`: range의 서로 다른 값 개수 (부서 수)
  - `share`: sumif ÷ sum(range) × 100 (비중 %)
- verify_numbers 불일치 항목: `location`, `expected`, `actual`, `source`(출처 파일이 없으면 없음).
  출처 파일은 검증 대상 문서 옆의 `<문서 이름>.provenance.json`이다.
  `source`는 그 위치 숫자의 provenance `source`와 같아야 한다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE = FIXTURES / "sales_q3.xlsx"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


async def _call(home: Path, name: str, args: dict[str, Any]) -> dict[str, Any]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "docmill.cli", "mcp"],
        env={**os.environ, "DOCMILL_HOME": str(home)},
        cwd=str(home),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(name, args)
    text = "".join(getattr(c, "text", "") for c in result.content)
    return dict(json.loads(text))


def _call_sync(home: Path, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(asyncio.wait_for(_call(home, name, args), 90))


def _report(home: Path) -> dict[str, Any]:
    return _call_sync(
        home,
        "excel_to_report_ppt",
        {
            "input": str(SAMPLE),
            "group_by": "부서",
            "value": "매출",
            "slides": 5,
            "chart": "bar",
            "verify": False,
        },
    )


def _prov_path(pptx: Path) -> Path:
    return pptx.with_name(pptx.name + ".provenance.json")


def _load_prov(pptx: Path) -> list[dict[str, Any]]:
    path = _prov_path(pptx)
    assert path.exists(), "결과 파일 옆에 provenance.json이 있어야 한다"
    return list(json.loads(path.read_text(encoding="utf-8"))["numbers"])


def _sheet_cells(path: Path) -> dict[str, str]:
    """표준 라이브러리만으로 xlsx 첫 시트의 셀 값을 {주소: 값}으로 읽는다."""
    with zipfile.ZipFile(path) as z:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            strings = ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")) for si in root]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    cells: dict[str, str] = {}
    for c in sheet.iter(f"{{{NS['m']}}}c"):
        inline = c.find("m:is", NS)
        v = c.find("m:v", NS)
        if inline is not None:
            cells[c.get("r", "")] = "".join(t.text or "" for t in inline.iter(f"{{{NS['m']}}}t"))
        elif v is not None and v.text is not None:
            cells[c.get("r", "")] = strings[int(v.text)] if c.get("t") == "s" else v.text
    return cells


def _range_cells(rng: str) -> list[str]:
    m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", rng)
    assert m, f"셀 범위 형식이 아니다: {rng}"
    assert m.group(1) == m.group(3), "열 하나의 범위여야 한다"
    return [f"{m.group(1)}{r}" for r in range(int(m.group(2)), int(m.group(4)) + 1)]


def _num(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        return None


def _reaggregate(src: dict[str, Any], cells: dict[str, str]) -> float:
    """provenance의 출처 정보만으로 원본 엑셀을 다시 집계한다."""
    agg = src["aggregation"]
    values = [cells.get(a, "") for a in _range_cells(src["range"])]
    if agg == "count_distinct":
        return float(len({v for v in values if v != ""}))
    numbers = [_num(v) for v in values]
    total = sum(n for n in numbers if n is not None)
    if agg == "sum":
        return total
    keys = [cells.get(a, "") for a in _range_cells(src["criteria_range"])]
    assert len(keys) == len(values), "range와 criteria_range 길이가 같아야 한다"
    picked = sum(
        n for n, k in zip(numbers, keys, strict=True) if n is not None and k == src["criteria"]
    )
    if agg == "sumif":
        return picked
    assert agg == "share", f"알 수 없는 집계 방식: {agg}"
    return picked / total * 100


def _tolerance(src: dict[str, Any]) -> float:
    # 비중은 문서에 소수 첫째 자리로 반올림해 보인다.
    return 0.05 + 1e-9 if src["aggregation"] == "share" else 1e-6


# 앞이 글자·숫자가 아닌 곳에서 시작하는 숫자. "영업1팀"의 1 같은 이름 속 숫자는 제외한다.
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _shape_numbers(pptx: Path) -> dict[tuple[int, str], list[float]]:
    """(슬라이드 번호, 도형 cNvPr id) → 그 도형 텍스트의 숫자 목록."""
    found: dict[tuple[int, str], list[float]] = {}
    with zipfile.ZipFile(pptx) as z:
        for name in z.namelist():
            m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name)
            if not m:
                continue
            root = ET.fromstring(z.read(name))
            for el in root.iter():
                if el.tag not in (f"{_P}sp", f"{_P}graphicFrame"):
                    continue
                c_nv = next(el.iter(f"{_P}cNvPr"), None)
                if c_nv is None:
                    continue
                text = "\n".join(t.text or "" for t in el.iter(f"{_A}t"))
                nums = [float(x.replace(",", "")) for x in _NUMBER.findall(text)]
                found[(int(m.group(1)), str(c_nv.get("id")))] = nums
    return found


def _slide_charts(pptx: Path) -> dict[int, set[str]]:
    """슬라이드 번호 → 그 슬라이드가 참조하는 그래프 파트 이름(chart1 등)."""
    found: dict[int, set[str]] = {}
    with zipfile.ZipFile(pptx) as z:
        for name in z.namelist():
            m = re.fullmatch(r"ppt/slides/_rels/slide(\d+)\.xml\.rels", name)
            if not m:
                continue
            root = ET.fromstring(z.read(name))
            charts = {
                Path(r.get("Target", "")).stem
                for r in root.iter(f"{_REL}Relationship")
                if re.search(r"charts/chart\d+\.xml$", r.get("Target", ""))
            }
            found[int(m.group(1))] = charts
    return found


def _slide_numbers(pptx: Path) -> dict[int, list[float]]:
    """슬라이드 번호 → 본문 텍스트에 보이는 숫자 목록."""
    found: dict[int, list[float]] = {}
    with zipfile.ZipFile(pptx) as z:
        for name in z.namelist():
            m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name)
            if not m:
                continue
            root = ET.fromstring(z.read(name))
            text = "\n".join(t.text or "" for t in root.iter(f"{_A}t"))
            nums = [float(x.replace(",", "")) for x in _NUMBER.findall(text)]
            if nums:
                found[int(m.group(1))] = nums
    return found


def _chart_points(pptx: Path) -> dict[tuple[str, int, int], float]:
    """(그래프 파트 이름, 계열 번호, 점 idx) → 값."""
    points: dict[tuple[str, int, int], float] = {}
    with zipfile.ZipFile(pptx) as z:
        for name in sorted(z.namelist()):
            if not re.fullmatch(r"ppt/charts/chart\d+\.xml", name):
                continue
            root = ET.fromstring(z.read(name))
            for s_no, ser in enumerate(root.iter(f"{_C}ser"), start=1):
                val = ser.find(f"{_C}val")
                assert val is not None
                for pos, pt in enumerate(val.iter(f"{_C}pt")):
                    v = pt.find(f"{_C}v")
                    if v is not None and v.text:
                        key = (Path(name).stem, s_no, int(pt.get("idx", pos)))
                        points[key] = float(v.text)
    return points


def _chart_key(n: dict[str, Any]) -> tuple[str, int, int]:
    loc = n["location"]
    return (str(loc["chart"]), int(loc["series"]), int(loc["point"]))


@pytest.mark.fr("FR-21")
def test_every_number_has_location_and_source(tmp_path: Path) -> None:
    out = _report(tmp_path)
    assert out.get("provenance"), "도구 결과에도 출처가 있어야 한다"
    pptx = Path(out["path"])
    numbers = _load_prov(pptx)
    assert {"summary", "chart", "detail"} <= {n["kind"] for n in numbers}
    shapes = _shape_numbers(pptx)
    slide_charts = _slide_charts(pptx)
    for n in numbers:
        loc = n["location"]
        assert loc["slide"] >= 1
        if n["kind"] == "chart":
            # 기록한 슬라이드가 실제로 그 그래프를 담고 있어야 한다.
            assert loc["chart"] in slide_charts.get(int(loc["slide"]), set()), n
        else:
            # 기록한 도형에 그 숫자가 실제로 보여야 한다.
            key = (int(loc["slide"]), str(loc["shape"]))
            assert key in shapes, f"없는 도형을 가리킨다: {n}"
            assert any(v == pytest.approx(float(n["value"])) for v in shapes[key]), (
                f"도형에 그 숫자가 없다: {n} / 도형 숫자 {shapes[key]}"
            )
        src = n["source"]
        assert src["sheet"] and src["range"] and src["aggregation"]
        assert Path(src["file"]).name == SAMPLE.name

    # 문서 쪽에서 출발해 누락을 찾는다: 그래프의 모든 점과 본문의 모든 숫자에 출처가 있어야 한다.
    chart_prov = {_chart_key(n): n for n in numbers if n["kind"] == "chart"}
    points = _chart_points(pptx)
    assert points, "그래프 값이 없다"
    assert set(points) == set(chart_prov), "그래프의 모든 점에 출처가 하나씩 있어야 한다"

    text_prov: dict[int, list[float]] = {}
    for n in numbers:
        if n["kind"] != "chart":
            text_prov.setdefault(int(n["location"]["slide"]), []).append(float(n["value"]))
    shown = _slide_numbers(pptx)
    assert shown, "본문에 숫자가 없다"
    for slide, values in shown.items():
        recorded = text_prov.get(slide, [])
        assert sorted(values) == pytest.approx(sorted(recorded)), (
            f"슬라이드 {slide}의 숫자 {values}와 출처 기록 {recorded}가 맞지 않는다"
        )
    assert set(text_prov) <= set(shown), "문서에 없는 숫자의 출처가 기록됐다"


@pytest.mark.fr("FR-21")
def test_reaggregating_source_with_provenance_matches_document(tmp_path: Path) -> None:
    pptx = Path(_report(tmp_path)["path"])
    numbers = _load_prov(pptx)
    cells = _sheet_cells(SAMPLE)
    points = _chart_points(pptx)
    for n in numbers:
        src = n["source"]
        again = _reaggregate(src, cells)
        assert again == pytest.approx(float(n["value"]), abs=_tolerance(src)), n
        # 기록된 값이 아니라 문서에 실제로 보이는 값과도 맞아야 한다.
        if n["kind"] == "chart":
            assert again == pytest.approx(points[_chart_key(n)], abs=_tolerance(src)), n
    # 본문 숫자는 test_every_number_has_location_and_source에서 기록 값과 1:1로 맞춘다.
    kinds = {n["source"]["aggregation"] for n in numbers}
    assert {"sum", "sumif"} <= kinds, "총합(sum)과 부서별 합계(sumif)가 있어야 한다"


def _tamper(src: Path, dst: Path) -> tuple[str, int, int]:
    """첫 그래프의 첫 계열 첫 값을 1 올린다. 바꾼 점의 (그래프, 계열, idx)를 돌려준다."""
    target: tuple[str, int, int] | None = None
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if target is None and re.fullmatch(r"ppt/charts/chart\d+\.xml", item.filename):
                text = data.decode("utf-8")
                m = re.search(r'<c:val>.*?<c:pt idx="(\d+)">\s*<c:v>([^<]+)</c:v>', text, re.S)
                if m:
                    changed = str(float(m.group(2)) + 1)
                    data = (text[: m.start(2)] + changed + text[m.end(2) :]).encode("utf-8")
                    target = (Path(item.filename).stem, 1, int(m.group(1)))
            zout.writestr(item, data)
    assert target is not None
    return target


@pytest.mark.fr("FR-21")
def test_verify_numbers_mismatch_shows_source_when_available(tmp_path: Path) -> None:
    report = Path(_report(tmp_path)["path"])
    numbers = _load_prov(report)
    args = {"source": str(SAMPLE), "group_by": "부서", "value": "매출"}

    # 출처 파일이 옆에 있는 경우: 위치와, 바꾼 그 숫자의 원래 출처가 함께 나온다.
    with_prov = tmp_path / "with_prov"
    with_prov.mkdir()
    key = _tamper(report, with_prov / "broken.pptx")
    _prov_path(with_prov / "broken.pptx").write_bytes(_prov_path(report).read_bytes())
    original = next(n for n in numbers if n["kind"] == "chart" and _chart_key(n) == key)
    res = _call_sync(
        tmp_path, "verify_numbers", {"document": str(with_prov / "broken.pptx"), **args}
    )
    assert len(res["mismatches"]) == 1, res
    first = res["mismatches"][0]
    assert first["location"]
    assert first["source"] == original["source"]

    # 출처 파일이 없는 경우: 위치만 나온다.
    bare = tmp_path / "bare"
    bare.mkdir()
    _tamper(report, bare / "broken.pptx")
    res2 = _call_sync(tmp_path, "verify_numbers", {"document": str(bare / "broken.pptx"), **args})
    assert len(res2["mismatches"]) == 1, res2
    first2 = res2["mismatches"][0]
    assert first2["location"]
    assert not first2.get("source")


@pytest.mark.fr("FR-21")
def test_provenance_is_byte_identical_for_same_input(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    pa = _prov_path(Path(_report(a)["path"]))
    pb = _prov_path(Path(_report(b)["path"]))
    assert pa.read_bytes() == pb.read_bytes()
