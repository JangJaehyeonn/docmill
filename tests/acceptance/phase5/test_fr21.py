"""FR-21 숫자 출처 추적 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_q3.xlsx (있음): 머리글 `부서·월·매출`, 5개 부서 × 3개월
가정(구현 방법이 정해지면 맞춰 고친다):
- 출처 파일 경로는 결과 pptx 경로 + `.provenance.json` 이다 (예: report.pptx.provenance.json).
- 도구 결과에 `provenance`(파일 경로)가 들어 있다.
- provenance.json은 `{"numbers": [...]}` 이고 각 항목은
  `value`, `location`(`slide` 번호와 `shape` 또는 `table_cell`), `kind`(summary/chart/detail),
  `source`(`file`·`sheet`·`range`·`aggregation`)를 가진다. aggregation은 `sum`.
- `range`는 `B2:B16`처럼 값 열 하나의 셀 범위다.
- verify_numbers 불일치 항목: `location`, `expected`, `actual`, `source`(출처 파일이 없으면 없음).
  출처 파일은 검증 대상 문서 옆의 `<문서 이름>.provenance.json`이다.
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
    assert m.group(1) == m.group(3), "값 열 하나의 범위여야 한다"
    return [f"{m.group(1)}{r}" for r in range(int(m.group(2)), int(m.group(4)) + 1)]


@pytest.mark.fr("FR-21")
def test_every_number_has_location_and_source(tmp_path: Path) -> None:
    out = _report(tmp_path)
    assert out.get("provenance"), "도구 결과에도 출처가 있어야 한다"
    numbers = _load_prov(Path(out["path"]))
    assert numbers
    assert {"summary", "chart", "detail"} <= {n["kind"] for n in numbers}
    for n in numbers:
        assert n["location"]["slide"] >= 1
        assert n["location"].get("shape") or n["location"].get("table_cell")
        src = n["source"]
        assert src["sheet"] and src["range"] and src["aggregation"]
        assert Path(src["file"]).name == SAMPLE.name


@pytest.mark.fr("FR-21")
def test_reaggregating_source_with_provenance_matches_document(tmp_path: Path) -> None:
    out = _report(tmp_path)
    numbers = _load_prov(Path(out["path"]))
    cells = _sheet_cells(SAMPLE)
    for n in numbers:
        src = n["source"]
        assert src["aggregation"] == "sum"
        values = [float(cells[a]) for a in _range_cells(src["range"]) if a in cells]
        assert sum(values) == pytest.approx(float(n["value"]))


def _tamper(src: Path, dst: Path) -> None:
    done = False
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if not done and re.fullmatch(r"ppt/charts/chart\d+\.xml", item.filename):
                text = data.decode("utf-8")
                m = re.search(r"<c:val>.*?<c:v>([^<]+)</c:v>", text, re.S)
                if m:
                    changed = str(float(m.group(1)) + 1)
                    data = (text[: m.start(1)] + changed + text[m.end(1) :]).encode("utf-8")
                    done = True
            zout.writestr(item, data)
    assert done


@pytest.mark.fr("FR-21")
def test_verify_numbers_mismatch_shows_source_when_available(tmp_path: Path) -> None:
    report = Path(_report(tmp_path)["path"])
    args = {"source": str(SAMPLE), "group_by": "부서", "value": "매출"}

    # 출처 파일이 옆에 있는 경우: 위치와 원본 출처가 함께 나온다.
    with_prov = tmp_path / "with_prov"
    with_prov.mkdir()
    _tamper(report, with_prov / "broken.pptx")
    _prov_path(with_prov / "broken.pptx").write_bytes(_prov_path(report).read_bytes())
    res = _call_sync(
        tmp_path, "verify_numbers", {"document": str(with_prov / "broken.pptx"), **args}
    )
    first = res["mismatches"][0]
    assert first["location"]
    assert first["source"]["sheet"] and first["source"]["range"]

    # 출처 파일이 없는 경우: 위치만 나온다.
    bare = tmp_path / "bare"
    bare.mkdir()
    _tamper(report, bare / "broken.pptx")
    res2 = _call_sync(tmp_path, "verify_numbers", {"document": str(bare / "broken.pptx"), **args})
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
