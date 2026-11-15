"""FR-13 레시피 관리 명령 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_week1.xlsx (아직 없음): 머리글 `부서·월·매출`.
가정(구현 방법이 정해지면 맞춰 고친다):
- `docmill recipes`는 저장된 레시피 이름을 출력한다.
- 이름 변경: `docmill recipes rename <기존> <새이름>`, 삭제: `docmill recipes delete <이름>`.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
RECIPE = "주간보고"
NEW_NAME = "월간보고"


async def _session(home: Path, calls: list[tuple[str, dict[str, Any]]]) -> list[str]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "docmill.cli", "mcp"],
        env={**os.environ, "DOCMILL_HOME": str(home)},
        cwd=str(home),
    )
    outs: list[str] = []
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        for name, args in calls:
            result = await session.call_tool(name, args)
            outs.append("".join(getattr(c, "text", "") for c in result.content))
    return outs


def _make_recipe(home: Path) -> None:
    report = {
        "input": str(FIXTURES / "sales_week1.xlsx"),
        "group_by": "부서",
        "value": "매출",
        "slides": 5,
        "chart": "bar",
    }
    calls = [("excel_to_report_ppt", report), ("save_recipe", {"name": RECIPE})]
    outs = asyncio.run(asyncio.wait_for(_session(home, calls), 120))
    assert all("error" not in json.loads(o) for o in outs), outs


def _cli(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "docmill.cli", *args],
        env={**os.environ, "DOCMILL_HOME": str(home)},
        cwd=str(home),
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.fr("FR-13")
def test_recipes_list_shows_saved_recipe(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    proc = _cli(tmp_path, "recipes")
    assert proc.returncode == 0, proc.stderr
    assert RECIPE in proc.stdout


@pytest.mark.fr("FR-13")
def test_rename_and_delete_are_reflected_in_list(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    assert _cli(tmp_path, "recipes", "rename", RECIPE, NEW_NAME).returncode == 0
    listing = _cli(tmp_path, "recipes").stdout
    assert NEW_NAME in listing and RECIPE not in listing
    assert _cli(tmp_path, "recipes", "delete", NEW_NAME).returncode == 0
    assert NEW_NAME not in _cli(tmp_path, "recipes").stdout
