"""FR-11 레시피 실행 전 점검 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_week1.xlsx, sales_week2.xlsx (아직 없음): 머리글 `부서·월·매출`.
가정(구현 방법이 정해지면 맞춰 고친다):
- run_recipe 도구 인자 `recipe`·`input`·`out_dir`. 점검 실패 시 결과 JSON에 `error`가 있고
  빠진 열 이름이 들어 있다.
- 열이 빠진 파일은 테스트 안에서 week1의 머리글 `매출`을 `금액`으로 바꿔 만든다
  (머리글이 sharedStrings.xml 또는 시트 XML에 있다고 가정).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
RECIPE = "주간보고"
VALUE = "매출"


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
    return asyncio.run(asyncio.wait_for(_session(home, calls), 120))


def _make_recipe(home: Path) -> None:
    report = {
        "input": str(FIXTURES / "sales_week1.xlsx"),
        "group_by": "부서",
        "value": VALUE,
        "slides": 5,
        "chart": "bar",
    }
    outs = _calls(home, [("excel_to_report_ppt", report), ("save_recipe", {"name": RECIPE})])
    assert "error" not in outs[0] and "error" not in outs[1], outs


def _rename_value_column(src: Path, dst: Path) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename in ("xl/sharedStrings.xml", "xl/worksheets/sheet1.xml"):
                data = data.decode("utf-8").replace(VALUE, "금액").encode("utf-8")
            zout.writestr(item, data)


@pytest.mark.fr("FR-11")
def test_missing_column_stops_without_files_and_names_the_column(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    broken = tmp_path / "broken.xlsx"
    _rename_value_column(FIXTURES / "sales_week1.xlsx", broken)
    out_dir = tmp_path / "out"
    args = {"recipe": RECIPE, "input": str(broken), "out_dir": str(out_dir)}
    out = _calls(tmp_path, [("run_recipe", args)])[0]
    assert "error" in out
    assert VALUE in json.dumps(out, ensure_ascii=False), "빠진 열 이름이 반환되어야 한다"
    assert not out_dir.exists() or not any(out_dir.rglob("*")), "아무 파일도 만들면 안 된다"
