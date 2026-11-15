"""FR-14 docmill run CLI 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_week1.xlsx, sales_week2.xlsx (아직 없음): 머리글 `부서·월·매출`.
가정(구현 방법이 정해지면 맞춰 고친다):
- 성공 시 stdout에 결과 파일 경로(.pptx)가 한 줄로 출력되고 종료 코드 0.
- 실패 시 종료 코드 1 이상이며 원인이 stdout 또는 stderr에 출력된다.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
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


@pytest.mark.fr("FR-14")
def test_one_line_run_prints_result_path(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    src = str(FIXTURES / "sales_week2.xlsx")
    proc = _cli(tmp_path, "run", RECIPE, "--input", src, "--out", str(tmp_path / "out"))
    assert proc.returncode == 0, proc.stderr
    paths = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().endswith(".pptx")]
    assert paths and Path(paths[-1]).exists()


@pytest.mark.fr("FR-14")
def test_failure_gives_nonzero_exit_code_and_cause(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    proc = _cli(tmp_path, "run", RECIPE, "--input", str(tmp_path / "없는파일.xlsx"))
    assert proc.returncode != 0
    assert (proc.stdout + proc.stderr).strip(), "원인이 출력되어야 한다"
    unknown = _cli(tmp_path, "run", "없는레시피", "--input", str(FIXTURES / "sales_week2.xlsx"))
    assert unknown.returncode != 0
    assert (unknown.stdout + unknown.stderr).strip()


@pytest.mark.fr("FR-14")
def test_run_finishes_within_ten_seconds(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    src = str(FIXTURES / "sales_week2.xlsx")
    start = time.monotonic()
    proc = _cli(tmp_path, "run", RECIPE, "--input", src, "--out", str(tmp_path / "out"))
    elapsed = time.monotonic() - start
    assert proc.returncode == 0, proc.stderr
    assert elapsed < 10
