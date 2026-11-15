"""FR-09 save_recipe 레시피 저장 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_week1.xlsx (아직 없음): 머리글 `부서·월·매출`.
가정(구현 방법이 정해지면 맞춰 고친다):
- save_recipe 도구 인자: `name`. 결과 JSON: `name`, `steps`(각 단계에 `tool`·`args`).
- 레시피의 입력 변수는 `$input`.
- 같은 MCP 세션에서 호출한 성공한 도구만 레시피에 들어간다.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
RECIPE = "주간보고"


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


def _report_args(**override: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "input": str(FIXTURES / "sales_week1.xlsx"),
        "group_by": "부서",
        "value": "매출",
        "slides": 5,
        "chart": "bar",
    }
    return {**args, **override}


@pytest.mark.fr("FR-09")
def test_save_recipe_after_report_returns_step_list(tmp_path: Path) -> None:
    outs = _calls(
        tmp_path,
        [("excel_to_report_ppt", _report_args()), ("save_recipe", {"name": RECIPE})],
    )
    assert "error" not in outs[0], outs[0]
    saved = outs[1]
    assert "error" not in saved, saved
    assert saved["name"] == RECIPE
    assert [s["tool"] for s in saved["steps"]] == ["excel_to_report_ppt"]


@pytest.mark.fr("FR-09")
def test_recipe_has_input_variable_instead_of_original_path(tmp_path: Path) -> None:
    outs = _calls(
        tmp_path,
        [("excel_to_report_ppt", _report_args()), ("save_recipe", {"name": RECIPE})],
    )
    dumped = json.dumps(outs[1]["steps"], ensure_ascii=False)
    assert "sales_week1" not in dumped, "원래 입력 파일 경로가 남으면 안 된다"
    assert "$input" in dumped


@pytest.mark.fr("FR-09")
def test_failed_run_is_not_included_in_recipe(tmp_path: Path) -> None:
    outs = _calls(
        tmp_path,
        [
            ("excel_to_report_ppt", _report_args()),
            ("excel_to_report_ppt", _report_args(value="없는열")),
            ("save_recipe", {"name": RECIPE}),
        ],
    )
    assert "error" in outs[1], "존재하지 않는 열로는 실패해야 한다"
    steps = outs[2]["steps"]
    assert len(steps) == 1
    assert "없는열" not in json.dumps(steps, ensure_ascii=False)
