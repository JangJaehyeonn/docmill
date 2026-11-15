"""FR-17 실행 기록 조회 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_week1.xlsx, sales_week2.xlsx (아직 없음): 머리글 `부서·월·매출`.
가정(구현 방법이 정해지면 맞춰 고친다):
- `docmill history <레시피>` 표에 '시간'·'소요'·'AI'·'검증' 열 머리글이 있고,
  AI 사용 여부는 '사용 안 함', 검증 결과는 '통과'로 표시된다.
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


@pytest.mark.fr("FR-17")
def test_history_table_shows_time_duration_ai_and_verification(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    src = str(FIXTURES / "sales_week2.xlsx")
    run = _cli(tmp_path, "run", RECIPE, "--input", src, "--out", str(tmp_path / "out"))
    assert run.returncode == 0, run.stderr
    proc = _cli(tmp_path, "history", RECIPE)
    assert proc.returncode == 0, proc.stderr
    for header in ("시간", "소요", "AI", "검증"):
        assert header in proc.stdout, f"표에 {header} 열이 있어야 한다"
    assert "사용 안 함" in proc.stdout
    assert "통과" in proc.stdout
