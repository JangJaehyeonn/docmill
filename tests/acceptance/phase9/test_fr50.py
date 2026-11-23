"""FR-50 폴더 감시 자동 실행 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_week1.xlsx, sales_week2.xlsx (있음): 머리글 `부서·월·매출`.
  `sales_week47.xlsx`는 테스트 안에서 sales_week2.xlsx를 복사해 만든다.
  열이 빠진 파일은 week1의 머리글 `매출`을 `금액`으로 바꿔 만든다.
가정(구현 방법이 정해지면 맞춰 고친다):
- CLI `docmill watch <레시피> --dir <감시폴더> --out <결과폴더>`는 종료될 때까지 계속 감시한다
  (테스트가 프로세스를 직접 끈다).
- 실행 기록은 `docmill.runlog.list_recipe_runs(recipe, home=...)`로 읽는다.
  점검 실패 기록은 `status`가 성공이 아니고 기록 안에 빠진 열 이름(`매출`)이 들어 있다.
- 결과 폴더에는 .pptx만 생긴다.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
RECIPE = "주간보고"
DEADLINE = 60


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


@contextmanager
def _watch(home: Path, watch_dir: Path, out_dir: Path) -> Iterator[subprocess.Popen[str]]:
    watch_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "docmill.cli", "watch", RECIPE]
    cmd += ["--dir", str(watch_dir), "--out", str(out_dir)]
    proc = subprocess.Popen(
        cmd,
        env={**os.environ, "DOCMILL_HOME": str(home)},
        cwd=str(home),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _wait(cond: Callable[[], bool], seconds: float = DEADLINE) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(1)
    return cond()


def _pptx(out_dir: Path) -> list[Path]:
    return sorted(out_dir.rglob("*.pptx")) if out_dir.exists() else []


def _runs(home: Path) -> list[dict[str, Any]]:
    from docmill import runlog

    return runlog.list_recipe_runs(RECIPE, home=home)


def _rename_value_column(src: Path, dst: Path) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename in ("xl/sharedStrings.xml", "xl/worksheets/sheet1.xml"):
                data = data.decode("utf-8").replace("매출", "금액").encode("utf-8")
            zout.writestr(item, data)


@pytest.mark.fr("FR-50")
def test_new_file_gets_report_within_60_seconds_and_is_logged(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    watch_dir, out_dir = tmp_path / "watch", tmp_path / "result"
    with _watch(tmp_path, watch_dir, out_dir):
        time.sleep(2)
        shutil.copy(FIXTURES / "sales_week2.xlsx", watch_dir / "sales_week47.xlsx")
        assert _wait(lambda: bool(_pptx(out_dir))), "60초 안에 결과 폴더에 보고서가 생겨야 한다"
    assert _runs(tmp_path), "실행 기록에 남아야 한다"


@pytest.mark.fr("FR-50")
def test_file_missing_column_makes_no_result_and_logs_preflight_failure(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    watch_dir, out_dir = tmp_path / "watch", tmp_path / "result"
    with _watch(tmp_path, watch_dir, out_dir):
        time.sleep(2)
        _rename_value_column(FIXTURES / "sales_week1.xlsx", watch_dir / "sales_bad.xlsx")
        assert _wait(lambda: bool(_runs(tmp_path))), "점검 실패 기록이 남아야 한다"
        time.sleep(3)
    assert _pptx(out_dir) == []
    record = _runs(tmp_path)[0]
    assert "매출" in json.dumps(record, ensure_ascii=False), "빠진 열 이름이 기록에 있어야 한다"
    assert record.get("status") not in ("success", "ok", "성공")


@pytest.mark.fr("FR-50")
def test_same_file_is_not_processed_twice(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    watch_dir, out_dir = tmp_path / "watch", tmp_path / "result"
    with _watch(tmp_path, watch_dir, out_dir):
        time.sleep(2)
        target = watch_dir / "sales_week47.xlsx"
        shutil.copy(FIXTURES / "sales_week2.xlsx", target)
        assert _wait(lambda: bool(_pptx(out_dir)))
        first = len(_runs(tmp_path))
        # 수정 시각만 바꾸고 감시 주기를 몇 번 더 기다린다.
        os.utime(target, None)
        time.sleep(15)
        assert len(_runs(tmp_path)) == first, "같은 파일을 두 번 처리하면 안 된다"
        assert len(_pptx(out_dir)) == 1
