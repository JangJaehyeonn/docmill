"""FR-05 excel_to_report_ppt 업무 도구 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_q3.xlsx (있음): 첫 행 머리글 `부서·월·매출`, 5개 부서 × 3개월 = 15행
가정(구현 방법이 정해지면 맞춰 고친다):
- 시트 첫 행이 머리글이고, 그룹 열 `GROUP_BY`와 값 열 `VALUE`가 있다.
- 도구 결과는 JSON 텍스트이며 `path`·`summary`·`run_id`를 가진다.
  verify=true면 `verification`에 `checked`(대조 개수)·`matched`(일치 개수)가 들어간다.
- 열이 없으면 `error`에 빠진 열 이름이 들어간다.
- 그래프 값은 pptx의 `ppt/charts/chart*.xml` 캐시(`c:val` 아래 `c:v`)로 확인한다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE = FIXTURES / "sales_q3.xlsx"
GROUP_BY = "부서"
VALUE = "매출"
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


def _run(home: Path, **overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "input": str(SAMPLE),
        "group_by": GROUP_BY,
        "value": VALUE,
        "slides": 5,
        "chart": "bar",
    }
    args.update(overrides)
    return asyncio.run(asyncio.wait_for(_call(home, "excel_to_report_ppt", args), 90))


def _expected_sums() -> dict[str, float]:
    """표준 라이브러리만으로 원본 엑셀의 group_by별 value 합계를 계산한다."""
    with zipfile.ZipFile(SAMPLE) as z:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            strings = ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")) for si in root]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows: list[dict[str, str]] = []
    for row in sheet.iter(f"{{{NS['m']}}}row"):
        cells: dict[str, str] = {}
        for c in row.findall("m:c", NS):
            col = re.match(r"[A-Z]+", c.get("r", ""))
            if col is None:
                continue
            inline = c.find("m:is", NS)
            v = c.find("m:v", NS)
            if inline is not None:
                cells[col.group()] = "".join(t.text or "" for t in inline.iter(f"{{{NS['m']}}}t"))
            elif v is not None and v.text is not None:
                cells[col.group()] = strings[int(v.text)] if c.get("t") == "s" else v.text
        rows.append(cells)
    header = {name: col for col, name in rows[0].items()}
    sums: dict[str, float] = defaultdict(float)
    for r in rows[1:]:
        sums[r[header[GROUP_BY]]] += float(r[header[VALUE]])
    return dict(sums)


def _chart_values(pptx: Path) -> list[list[float]]:
    """차트마다 값 목록을 돌려준다 (슬라이드 여러 장에 차트가 여러 개일 수 있다)."""
    charts: list[list[float]] = []
    with zipfile.ZipFile(pptx) as z:
        for name in sorted(z.namelist()):
            if re.fullmatch(r"ppt/charts/chart\d+\.xml", name):
                text = z.read(name).decode("utf-8")
                values: list[float] = []
                for block in re.findall(r"<c:val>.*?</c:val>", text, re.S):
                    values += [float(v) for v in re.findall(r"<c:v>([^<]+)</c:v>", block)]
                charts.append(values)
    return charts


def _slide_count(pptx: Path) -> int:
    with zipfile.ZipFile(pptx) as z:
        return len([n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)])


@pytest.mark.fr("FR-05")
@pytest.mark.parametrize("slides", [5, 6])
def test_slide_count_matches_slides_argument(tmp_path: Path, slides: int) -> None:
    out = _run(tmp_path, slides=slides)
    path = Path(out["path"])
    assert path.suffix == ".pptx" and path.exists()
    assert _slide_count(path) == slides


@pytest.mark.fr("FR-05")
def test_chart_values_equal_group_sums(tmp_path: Path) -> None:
    out = _run(tmp_path)
    expected = sorted(_expected_sums().values())
    charts = _chart_values(Path(out["path"]))
    assert charts, "그래프가 없다"
    assert any(sorted(values) == pytest.approx(expected) for values in charts), (
        "group_by별 value 합계를 그린 그래프가 없다"
    )


@pytest.mark.fr("FR-05")
def test_verify_true_reports_counts(tmp_path: Path) -> None:
    out = _run(tmp_path, verify=True)
    verification = out["verification"]
    assert verification["checked"] > 0
    assert verification["matched"] == verification["checked"]


@pytest.mark.fr("FR-05")
@pytest.mark.parametrize("missing", ["group_by", "value"])
def test_missing_column_error_names_the_column(tmp_path: Path, missing: str) -> None:
    bad = "없는열_XYZ"
    out = _run(tmp_path, **{missing: bad})
    assert "error" in out
    assert bad in json.dumps(out, ensure_ascii=False)
    assert not list(tmp_path.rglob("*.pptx")), "실패했으면 pptx가 생기면 안 된다"


@pytest.mark.fr("FR-05")
def test_result_has_path_summary_and_run_id(tmp_path: Path) -> None:
    out = _run(tmp_path)
    assert Path(out["path"]).exists()
    assert out["summary"]
    assert out["run_id"]
