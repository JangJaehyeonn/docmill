"""FR-37 살아 있는 문서 (refresh_links) 인수 테스트 (초안).

필요한 샘플: tests/fixtures/sales_q3.xlsx (있음. 영업1팀 값은 C2=1200 등 2·7·12행).
가정(구현 방법이 정해지면 맞춰 고친다):
- MCP 도구 `refresh_links` 인자: `document`(pptx). 문서 옆 `<이름>.provenance.json`(FR-21)을 연결로 본다.
  provenance `source.file`은 excel_to_report_ppt에 준 원본 경로이므로 테스트는 원본의 복사본을 입력으로 쓴다.
- 결과 JSON: `changes` 목록. 각 항목은 `location`, `old`, `new`. 바뀐 숫자만 나온다.
  고친 뒤 provenance.json도 새 값으로 갱신된다. 고친 파일은 `document` 자리에서 고친다.
- studio: 작업 폴더에 원본 xlsx와 연결된 pptx(+provenance)가 있고 둘 다 탭이 열린 상태에서
  원본 파일이 바뀌면 화면에 '원본이 바뀌었어요' 문구와 `새 숫자로 고치기` 버튼이 나온다.
  ONLYOFFICE가 필요하므로 DOCMILL_ONLYOFFICE_URL이 없으면 실패한다.
"""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE = FIXTURES / "sales_q3.xlsx"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")

_FR = "FR-37"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


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


def _sync(home: Path, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(asyncio.wait_for(_call(home, name, args), 90))


def _make_report(home: Path, source: Path) -> Path:
    out = _sync(
        home,
        "excel_to_report_ppt",
        {
            "input": str(source),
            "group_by": "부서",
            "value": "매출",
            "slides": 5,
            "chart": "bar",
            "verify": False,
        },
    )
    return Path(out["path"])


def _prov_path(pptx: Path) -> Path:
    return pptx.with_name(pptx.name + ".provenance.json")


def _prov(pptx: Path) -> list[dict[str, Any]]:
    return list(json.loads(_prov_path(pptx).read_text(encoding="utf-8"))["numbers"])


def _bump_team1(src: Path, dst: Path) -> None:
    """원본 xlsx의 영업1팀 첫 값(C2)을 1200 → 1500으로 바꾼 파일을 dst에 쓴다."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                text = data.decode("utf-8")
                new = text.replace('<c r="C2" t="n"><v>1200</v>', '<c r="C2" t="n"><v>1500</v>', 1)
                assert new != text, "C2 셀을 찾지 못했다"
                data = new.encode("utf-8")
            zout.writestr(item, data)


def _cells(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as z:
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    cells: dict[str, str] = {}
    for c in sheet.iter(f"{{{NS['m']}}}c"):
        inline = c.find("m:is", NS)
        v = c.find("m:v", NS)
        if inline is not None:
            cells[c.get("r", "")] = "".join(t.text or "" for t in inline.iter(f"{{{NS['m']}}}t"))
        elif v is not None and v.text is not None:
            cells[c.get("r", "")] = v.text
    return cells


def _range(rng: str) -> list[str]:
    m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", rng)
    assert m
    return [f"{m.group(1)}{r}" for r in range(int(m.group(2)), int(m.group(4)) + 1)]


def _reaggregate(src: dict[str, Any], cells: dict[str, str]) -> float:
    agg = src["aggregation"]
    values = [cells.get(a, "") for a in _range(src["range"])]
    if agg == "count_distinct":
        return float(len({v for v in values if v != ""}))
    nums = [float(v) if re.fullmatch(r"-?[\d.]+", v) else None for v in values]
    total = sum(n for n in nums if n is not None)
    if agg == "sum":
        return total
    keys = [cells.get(a, "") for a in _range(src["criteria_range"])]
    picked = sum(n for n, k in zip(nums, keys, strict=True) if n is not None and k == src["criteria"])
    return picked if agg == "sumif" else picked / total * 100


def _tol(src: dict[str, Any]) -> float:
    return 0.05 + 1e-9 if src["aggregation"] == "share" else 1e-6


def _chart_points(pptx: Path) -> dict[tuple[str, int, int], float]:
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
                        points[(Path(name).stem, s_no, int(pt.get("idx", pos)))] = float(v.text)
    return points


def _shape_numbers(pptx: Path) -> dict[tuple[int, str], list[float]]:
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
                found[(int(m.group(1)), str(c_nv.get("id")))] = [
                    float(x.replace(",", "")) for x in _NUMBER.findall(text)
                ]
    return found


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "src" / "sales_q3.xlsx"
    src.parent.mkdir()
    shutil.copy(SAMPLE, src)
    return src, _make_report(tmp_path, src)


@pytest.mark.fr("FR-37")
def test_refresh_makes_document_equal_to_reaggregated_source(tmp_path: Path) -> None:
    src, report = _setup(tmp_path)
    before = _prov(report)
    _bump_team1(SAMPLE, src)
    out = _sync(tmp_path, "refresh_links", {"document": str(report)})
    assert out.get("changes"), out
    cells = _cells(src)
    points = _chart_points(report)
    shapes = _shape_numbers(report)
    assert before, "provenance가 있어야 한다"
    for n in _prov(report):  # 고친 뒤의 provenance
        again = _reaggregate(n["source"], cells)
        assert again == pytest.approx(float(n["value"]), abs=_tol(n["source"])), n
        loc = n["location"]
        if n["kind"] == "chart":
            key = (str(loc["chart"]), int(loc["series"]), int(loc["point"]))
            assert points[key] == pytest.approx(again, abs=_tol(n["source"])), n
        else:
            seen = shapes[(int(loc["slide"]), str(loc["shape"]))]
            assert any(v == pytest.approx(again, abs=_tol(n["source"])) for v in seen), (n, seen)


@pytest.mark.fr("FR-37")
def test_change_list_has_old_and_new_and_only_changed_numbers(tmp_path: Path) -> None:
    src, report = _setup(tmp_path)
    before = _prov(report)
    _bump_team1(SAMPLE, src)
    cells = _cells(src)
    expected = [
        n
        for n in before
        if abs(_reaggregate(n["source"], cells) - float(n["value"])) > _tol(n["source"])
    ]
    unchanged = [n for n in before if n not in expected]
    assert expected and unchanged, "샘플 자체가 바뀐 것과 안 바뀐 것을 모두 가져야 한다"
    out = _sync(tmp_path, "refresh_links", {"document": str(report)})
    changes = out["changes"]
    assert len(changes) == len(expected), changes
    for ch in changes:
        assert ch["location"] and "old" in ch and "new" in ch
    old_new = sorted((float(c["old"]), float(c["new"])) for c in changes)
    want = sorted((float(n["value"]), _reaggregate(n["source"], cells)) for n in expected)
    assert old_new == pytest.approx(want, abs=0.06)


@pytest.mark.fr("FR-37")
def test_unchanged_source_leaves_file_byte_identical_and_list_empty(tmp_path: Path) -> None:
    _, report = _setup(tmp_path)
    before = report.read_bytes()
    prov_before = _prov_path(report).read_bytes()
    out = _sync(tmp_path, "refresh_links", {"document": str(report)})
    assert out["changes"] == []
    assert report.read_bytes() == before
    assert _prov_path(report).read_bytes() == prov_before


@pytest.mark.fr("FR-37")
def test_studio_suggests_refresh_when_source_changes(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    assert os.environ.get("DOCMILL_ONLYOFFICE_URL"), "DOCMILL_ONLYOFFICE_URL이 필요하다"
    ws = tmp_path / "ws"
    ws.mkdir()
    src = ws / "sales_q3.xlsx"
    shutil.copy(SAMPLE, src)
    report = _make_report(tmp_path, src)
    shutil.copy(report, ws / "report.pptx")
    shutil.copy(_prov_path(report), ws / "report.pptx.provenance.json")
    page.goto(start_studio(workspace=ws))
    for name in ("sales_q3.xlsx", "report.pptx"):
        page.get_by_role("listitem").filter(has_text=name).first.click()
        page.get_by_role("tab", name=name).wait_for()
    _bump_team1(SAMPLE, tmp_path / "changed.xlsx")
    shutil.copy(tmp_path / "changed.xlsx", src)  # 원본이 저장된 것과 같은 효과
    page.get_by_text("원본이 바뀌었어요").wait_for(timeout=60_000)
    page.get_by_role("button", name="새 숫자로 고치기").wait_for()
