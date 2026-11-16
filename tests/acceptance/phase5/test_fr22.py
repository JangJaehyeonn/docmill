"""FR-22 레시피 입력 변화 감지 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_week1.xlsx, sales_week2.xlsx (있음): 머리글 `부서·월·매출`
- 열 이름만 바뀐 입력(`매출` → `금액`)은 테스트 안에서 sales_week2.xlsx의 zip을 고쳐 만든다.
가정(구현 방법이 정해지면 맞춰 고친다):
- 저장된 레시피 내용은 `docmill recipes show <이름> --json` 출력의 `input_schema`로 확인한다.
  `input_schema`는 `sheets`(시트 이름 목록)·`columns`(열 이름 목록)·
  `column_types`(열 이름 → 형식)를 가진다.
- 구조가 다르면 0이 아닌 종료 코드, stdout에 JSON
  `{"suggestions": [{"changed": 새 열, "original": 원래 열, "reason": 근거}]}` 를 출력한다.
- `--map 금액=매출`은 `새 열=원래 열` 형식이다.
- 성공 시 stdout에 결과 파일 경로(.pptx)가 한 줄로 나온다.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import zipfile
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


def _cli(
    home: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "docmill.cli", *args],
        env={**os.environ, "DOCMILL_HOME": str(home), **(env or {})},
        cwd=str(home),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _renamed_copy(dst: Path) -> Path:
    """sales_week2.xlsx에서 열 이름 `매출`을 `금액`으로 바꾼 복사본을 만든다."""
    src = FIXTURES / "sales_week2.xlsx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.startswith("xl/") and item.filename.endswith(".xml"):
                data = data.decode("utf-8").replace("매출", "금액").encode("utf-8")
            zout.writestr(item, data)
    return dst


def _run(
    home: Path, src: Path, out: Path, *extra: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return _cli(home, "run", RECIPE, "--input", str(src), "--out", str(out), *extra, env=env)


def _result_path(proc: subprocess.CompletedProcess[str]) -> Path:
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().endswith(".pptx")]
    assert lines, proc.stdout
    return Path(lines[-1])


def _chart_xml(pptx: Path) -> list[bytes]:
    with zipfile.ZipFile(pptx) as z:
        return [z.read(n) for n in sorted(z.namelist()) if n.startswith("ppt/charts/chart")]


@pytest.mark.fr("FR-22")
def test_recipe_contains_input_structure(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    proc = _cli(tmp_path, "recipes", "show", RECIPE, "--json")
    assert proc.returncode == 0, proc.stderr
    schema = json.loads(proc.stdout)["input_schema"]
    assert schema["sheets"]
    assert {"부서", "월", "매출"} <= set(schema["columns"])
    assert set(schema["column_types"]) >= {"부서", "월", "매출"}
    assert schema["column_types"]["매출"]


@pytest.mark.fr("FR-22")
def test_renamed_column_fails_with_mapping_suggestion(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    changed = _renamed_copy(tmp_path / "changed.xlsx")
    out = tmp_path / "out"
    proc = _run(tmp_path, changed, out)
    assert proc.returncode != 0
    assert not (out.exists() and list(out.rglob("*.pptx"))), "결과 파일을 쓰면 안 된다"
    suggestions = json.loads(proc.stdout)["suggestions"]
    hit = [s for s in suggestions if s["changed"] == "금액" and s["original"] == "매출"]
    assert hit and hit[0]["reason"]


@pytest.mark.fr("FR-22")
def test_map_option_runs_and_matches_original_result(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    changed = _renamed_copy(tmp_path / "changed.xlsx")
    mapped = _run(tmp_path, changed, tmp_path / "out_map", "--map", "금액=매출")
    assert mapped.returncode == 0, mapped.stderr
    plain = _run(tmp_path, FIXTURES / "sales_week2.xlsx", tmp_path / "out_plain")
    assert plain.returncode == 0, plain.stderr
    assert _chart_xml(_result_path(mapped)) == _chart_xml(_result_path(plain))


@pytest.mark.fr("FR-22")
def test_suggestion_is_deterministic_and_offline(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    changed = _renamed_copy(tmp_path / "changed.xlsx")
    offline = {
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "ANTHROPIC_API_KEY": "",
    }
    first = _run(tmp_path, changed, tmp_path / "o1", env=offline)
    second = _run(tmp_path, changed, tmp_path / "o2", env=offline)
    assert first.returncode != 0
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["suggestions"]
