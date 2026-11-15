"""FR-12 레시피 자동 검증 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_week1.xlsx, sales_week2.xlsx (아직 없음): 머리글 `부서·월·매출`.
가정(구현 방법이 정해지면 맞춰 고친다):
- 검증 실패 조작: 환경 변수 `DOCMILL_TEST_FORCE_VERIFY_FAIL=1`이면 레시피 실행 중 숫자 검증이
  불일치를 낸다 (테스트용 훅. 구현 방법이 정해지면 바꾼다).
- `docmill run <레시피> --input <파일> --out <폴더>`로 실행하고,
  `docmill history <레시피>` 출력에 '실패'와 원인('검증' 또는 '불일치')이 나온다.
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


def _cli(home: Path, *args: str, force_fail: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DOCMILL_HOME": str(home)}
    if force_fail:
        env["DOCMILL_TEST_FORCE_VERIFY_FAIL"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "docmill.cli", *args],
        env=env,
        cwd=str(home),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _run_forced_failure(home: Path, out_dir: Path) -> subprocess.CompletedProcess[str]:
    src = str(FIXTURES / "sales_week2.xlsx")
    return _cli(home, "run", RECIPE, "--input", src, "--out", str(out_dir), force_fail=True)


@pytest.mark.fr("FR-12")
def test_failed_verification_leaves_no_output_file(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    out_dir = tmp_path / "out"
    proc = _run_forced_failure(tmp_path, out_dir)
    assert proc.returncode != 0
    assert not out_dir.exists() or not any(out_dir.rglob("*.pptx"))


@pytest.mark.fr("FR-12")
def test_failure_is_logged_with_cause(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    _run_forced_failure(tmp_path, tmp_path / "out")
    hist = _cli(tmp_path, "history", RECIPE)
    text = hist.stdout + hist.stderr
    assert "실패" in text
    assert "검증" in text or "불일치" in text, "실패 원인이 기록되어야 한다"
