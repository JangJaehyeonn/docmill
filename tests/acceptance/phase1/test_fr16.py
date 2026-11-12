"""FR-16 실행 로그 인수 테스트 (초안).

필요한 샘플: tests/fixtures/sales_q3.xlsx (비밀값 테스트의 입력 경로로만 쓴다).
가정(구현 방법이 정해지면 맞춰 고친다):
- 로그 저장 폴더는 환경 변수 `DOCMILL_HOME`으로 지정하고, 그 아래 SQLite 파일
  (*.db, *.sqlite, *.sqlite3)에 쌓인다.
- 로그 테이블에는 실행 ID 열 `run_id`와 도구 이름이 들어간다.
- `docmill.runlog.get_run(run_id, home=...)`이 실행 ID로 기록(dict)을 조회한다.
- 테스트는 테이블 이름을 가정하지 않고 모든 테이블의 모든 행을 읽어 확인한다.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SECRET = "sk-test-SECRET-0123456789abcdef"


async def _call_tools(home: Path, calls: list[tuple[str, dict[str, Any]]]) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "docmill.cli", "mcp"],
        env={**os.environ, "DOCMILL_HOME": str(home)},
        cwd=str(home),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        for name, args in calls:
            await session.call_tool(name, args)


def _read_all_rows(home: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    dbs = [p for p in home.rglob("*") if p.suffix in {".db", ".sqlite", ".sqlite3"}]
    assert dbs, "SQLite 로그 파일이 생기지 않았다"
    for db in dbs:
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        tables = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (table,) in tables:
            rows += [dict(r) for r in con.execute(f'SELECT * FROM "{table}"')]
        con.close()
    return rows


@pytest.mark.fr("FR-16")
def test_each_call_gets_run_id_and_log_row(tmp_path: Path) -> None:
    asyncio.run(asyncio.wait_for(_call_tools(tmp_path, [("ping", {}), ("ping", {})]), 60))
    rows = [r for r in _read_all_rows(tmp_path) if "ping" in str(list(r.values()))]
    assert len(rows) == 2, "호출 두 번에 로그 두 줄이 남아야 한다"
    run_ids = {r["run_id"] for r in rows}
    assert len(run_ids) == 2 and all(run_ids), "호출마다 서로 다른 실행 ID가 있어야 한다"


@pytest.mark.fr("FR-16")
def test_log_row_can_be_found_by_run_id(tmp_path: Path) -> None:
    asyncio.run(asyncio.wait_for(_call_tools(tmp_path, [("ping", {})]), 60))
    from docmill import runlog

    row = next(r for r in _read_all_rows(tmp_path) if "ping" in str(list(r.values())))
    record = runlog.get_run(row["run_id"], home=tmp_path)
    assert record is not None
    assert record["tool"] == "ping"
    # 도구명·인자 요약·결과·소요 시간·AI 사용 여부가 기록된다
    for key in ("tool", "args_summary", "result", "duration_ms", "ai_used"):
        assert key in record


@pytest.mark.fr("FR-16")
def test_args_summary_excludes_large_data_and_secrets(tmp_path: Path) -> None:
    big = "X" * 20000
    args = {
        "input": str(FIXTURES / "sales_q3.xlsx"),
        "group_by": big + SECRET,
        "value": "매출",
    }
    asyncio.run(asyncio.wait_for(_call_tools(tmp_path, [("excel_to_report_ppt", args)]), 60))
    rows = _read_all_rows(tmp_path)
    assert any("excel_to_report_ppt" in str(list(r.values())) for r in rows), (
        "호출이 기록되지 않았다"
    )
    for row in rows:
        for value in row.values():
            text = str(value)
            assert SECRET not in text, "비밀값이 로그에 남았다"
            assert big not in text, "큰 데이터가 로그에 통째로 남았다"
            assert len(text) < 5000
