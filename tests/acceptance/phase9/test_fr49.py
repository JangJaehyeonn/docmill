"""FR-49 보고 배우는 자동화 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_week1.xlsx, sales_week2.xlsx (있음): 머리글 `부서·월·매출`.
  반복 입력은 테스트 안에서 이 파일을 복사해 만든다 (입력 파일 이름만 다름).
가정(구현 방법이 정해지면 맞춰 고친다):
- 같은 MCP 세션에서 excel_to_report_ppt를 입력 파일만 바꿔 N번 호출하면 실행 로그에 남는다.
- CLI `docmill recipes suggest`: stdout에 JSON `{"suggestions": [{"message": str, "count": int}]}`.
  제안이 없으면 suggestions는 빈 목록. 문구에는 '레시피로 저장할까요'가 들어간다.
- CLI `docmill recipes suggest --accept <이름>`: 제안을 수락해 save_recipe와 같은 형식으로 저장한다.
  `docmill recipes show <이름>` 출력에서 입력 경로가 `$변수`로 보인다.
- run_recipe 도구 인자 `recipe`·`input`·`out_dir`, 결과 JSON `path`. 실패 시 `error`.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
RECIPE = "자동제안레시피"


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


def _cli(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "docmill.cli", *args],
        env={**os.environ, "DOCMILL_HOME": str(home)},
        cwd=str(home),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _inputs(tmp_path: Path, n: int) -> list[Path]:
    """같은 형식, 이름만 다른 입력 파일 n개."""
    files = []
    for i in range(n):
        src = FIXTURES / ("sales_week1.xlsx" if i % 2 == 0 else "sales_week2.xlsx")
        dst = tmp_path / "in" / f"sales_copy{i}.xlsx"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
        files.append(dst)
    return files


def _report_calls(files: list[Path]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "excel_to_report_ppt",
            {"input": str(f), "group_by": "부서", "value": "매출", "slides": 5, "chart": "bar"},
        )
        for f in files
    ]


def _suggestions(home: Path) -> list[dict[str, Any]]:
    proc = _cli(home, "recipes", "suggest")
    assert proc.returncode == 0, proc.stderr
    return list(json.loads(proc.stdout)["suggestions"])


@pytest.mark.fr("FR-49")
def test_suggestion_appears_after_three_repeats(tmp_path: Path) -> None:
    outs = _calls(tmp_path, _report_calls(_inputs(tmp_path, 3)))
    assert all("error" not in o for o in outs), outs
    found = _suggestions(tmp_path)
    assert found, "같은 순서가 3번 반복되면 제안이 있어야 한다"
    assert any("레시피로 저장할까요" in s["message"] for s in found)


@pytest.mark.fr("FR-49")
def test_no_suggestion_after_two_repeats(tmp_path: Path) -> None:
    outs = _calls(tmp_path, _report_calls(_inputs(tmp_path, 2)))
    assert all("error" not in o for o in outs), outs
    assert _suggestions(tmp_path) == []


@pytest.mark.fr("FR-49")
def test_accepting_saves_recipe_with_variable_and_it_runs(tmp_path: Path) -> None:
    files = _inputs(tmp_path, 3)
    outs = _calls(tmp_path, _report_calls(files))
    assert all("error" not in o for o in outs), outs
    accept = _cli(tmp_path, "recipes", "suggest", "--accept", RECIPE)
    assert accept.returncode == 0, accept.stderr

    shown = _cli(tmp_path, "recipes", "show", RECIPE)
    assert shown.returncode == 0, shown.stderr
    assert "$" in shown.stdout, "입력 파일이 $변수로 바뀌어야 한다"
    for f in files:
        assert str(f) not in shown.stdout, "원래 입력 경로가 남아 있으면 안 된다"

    args = {
        "recipe": RECIPE,
        "input": str(FIXTURES / "sales_week2.xlsx"),
        "out_dir": str(tmp_path / "out"),
    }
    ran = _calls(tmp_path, [("run_recipe", args)])[0]
    assert "error" not in ran, ran
    assert Path(ran["path"]).suffix == ".pptx" and Path(ran["path"]).exists()
