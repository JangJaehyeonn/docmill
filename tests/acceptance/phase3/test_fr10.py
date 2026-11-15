"""FR-10 run_recipe 레시피 실행 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_week1.xlsx, sales_week2.xlsx (아직 없음):
  같은 형식(머리글 `부서·월·매출`), 다른 숫자.
가정(구현 방법이 정해지면 맞춰 고친다):
- save_recipe 도구 인자 `name`, run_recipe 도구 인자 `recipe`·`input`·선택 `out_dir`.
- run_recipe 결과 JSON: `path`(생성된 pptx), `run_id`. 실패 시 `error`.
- 로그의 AI 사용 여부는 `docmill.runlog.get_run(run_id, home=...)["ai_used"]`가 False.
- 기대 숫자는 표준 라이브러리(zip+XML)로 엑셀(첫 시트, 첫 행 머리글)을 읽어 부서별 합계로 계산한다.
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
RECIPE = "주간보고"
_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _parse(result: Any) -> dict[str, Any]:
    text = "".join(getattr(c, "text", "") for c in result.content)
    try:
        return dict(json.loads(text))
    except ValueError:
        return {"error": text}


async def _session(home: Path, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "docmill.cli", "mcp"],
        env={**os.environ, "DOCMILL_HOME": str(home)},
        cwd=str(home),
    )
    outs: list[dict[str, Any]] = []
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        for name, args in calls:
            outs.append(_parse(await session.call_tool(name, args)))
    return outs


def _calls(home: Path, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return asyncio.run(asyncio.wait_for(_session(home, calls), 180))


def _make_recipe(home: Path) -> None:
    report = {
        "input": str(FIXTURES / "sales_week1.xlsx"),
        "group_by": "부서",
        "value": "매출",
        "slides": 5,
        "chart": "bar",
    }
    outs = _calls(home, [("excel_to_report_ppt", report), ("save_recipe", {"name": RECIPE})])
    assert "error" not in outs[0] and "error" not in outs[1], outs


def _run_args(src: Path, out_dir: Path) -> dict[str, Any]:
    return {"recipe": RECIPE, "input": str(src), "out_dir": str(out_dir)}


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():  # type: ignore[union-attr]
        n = n * 26 + ord(ch) - 64
    return n - 1


def _group_sums(xlsx: Path) -> dict[str, float]:
    with zipfile.ZipFile(xlsx) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(f"{_M}t")) for si in root]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows: list[dict[int, str]] = []
    for row in sheet.iter(f"{_M}row"):
        cells: dict[int, str] = {}
        for c in row.findall(f"{_M}c"):
            v = c.find(f"{_M}v")
            if v is None:
                continue
            cells[_col(c.get("r", "A"))] = (
                shared[int(v.text or "0")] if c.get("t") == "s" else (v.text or "")
            )
        rows.append(cells)
    header = {name: i for i, name in rows[0].items()}
    sums: dict[str, float] = defaultdict(float)
    for r in rows[1:]:
        sums[r[header["부서"]]] += float(r[header["매출"]])
    return dict(sums)


def _chart_values(pptx: Path) -> list[float]:
    vals: list[float] = []
    with zipfile.ZipFile(pptx) as z:
        for name in sorted(z.namelist()):
            if re.fullmatch(r"ppt/charts/chart\d+\.xml", name):
                text = z.read(name).decode("utf-8")
                for block in re.findall(r"<c:val>(.*?)</c:val>", text, re.S):
                    vals += [float(x) for x in re.findall(r"<c:v>([^<]+)</c:v>", block)]
    return vals


def _fingerprint(pptx: Path) -> dict[str, bytes]:
    """문서 속성(docProps)을 뺀 모든 부품의 바이트. 내용·서식 비교용."""
    with zipfile.ZipFile(pptx) as z:
        return {n: z.read(n) for n in sorted(z.namelist()) if not n.startswith("docProps/")}


@pytest.mark.fr("FR-10")
def test_other_week_input_gives_pptx_with_new_numbers(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    week2 = FIXTURES / "sales_week2.xlsx"
    out = _calls(tmp_path, [("run_recipe", _run_args(week2, tmp_path / "out"))])[0]
    assert "error" not in out, out
    pptx = Path(out["path"])
    assert pptx.suffix == ".pptx" and pptx.exists()
    actual = sorted(_chart_values(pptx))
    assert actual == pytest.approx(sorted(_group_sums(week2).values())), (
        "숫자는 새 파일 기준이어야 한다"
    )
    assert actual != pytest.approx(sorted(_group_sums(FIXTURES / "sales_week1.xlsx").values()))


@pytest.mark.fr("FR-10")
def test_ten_runs_give_identical_content_and_format(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    week2 = FIXTURES / "sales_week2.xlsx"
    calls = [("run_recipe", _run_args(week2, tmp_path / f"out{i}")) for i in range(10)]
    outs = _calls(tmp_path, calls)
    assert all("error" not in o for o in outs), outs
    prints = [_fingerprint(Path(o["path"])) for o in outs]
    assert all(p == prints[0] for p in prints)


@pytest.mark.fr("FR-10")
def test_run_log_records_ai_not_used(tmp_path: Path) -> None:
    from docmill import runlog

    _make_recipe(tmp_path)
    week2 = FIXTURES / "sales_week2.xlsx"
    out = _calls(tmp_path, [("run_recipe", _run_args(week2, tmp_path / "out"))])[0]
    record = runlog.get_run(out["run_id"], home=tmp_path)
    assert record is not None
    assert record["ai_used"] is False
