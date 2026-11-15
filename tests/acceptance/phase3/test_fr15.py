"""FR-15 실행 결과 전달 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_week1.xlsx, sales_week2.xlsx (아직 없음): 머리글 `부서·월·매출`.
가정(구현 방법이 정해지면 맞춰 고친다):
- 웹훅 URL은 환경 변수 `DOCMILL_WEBHOOK_URL`로 설정하고, `docmill run` 실행이 끝나면
  요약(레시피 이름 포함)을 POST로 보낸다.
- 가짜 서버는 테스트 안에서 http.server 스레드로 띄운다 (127.0.0.1, 임의 포트).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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


def _fake_server(status: int, received: list[bytes]) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            received.append(self.rfile.read(length))
            self.send_response(status)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _run_with_webhook(home: Path, url: str) -> subprocess.CompletedProcess[str]:
    src = str(FIXTURES / "sales_week2.xlsx")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "docmill.cli",
            "run",
            RECIPE,
            "--input",
            src,
            "--out",
            str(home / "out"),
        ],
        env={**os.environ, "DOCMILL_HOME": str(home), "DOCMILL_WEBHOOK_URL": url},
        cwd=str(home),
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.fr("FR-15")
def test_summary_is_sent_to_webhook_after_run(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    received: list[bytes] = []
    server = _fake_server(200, received)
    try:
        proc = _run_with_webhook(tmp_path, f"http://127.0.0.1:{server.server_port}/hook")
    finally:
        server.shutdown()
    assert proc.returncode == 0, proc.stderr
    assert len(received) == 1
    assert RECIPE in received[0].decode("utf-8")


@pytest.mark.fr("FR-15")
def test_webhook_failure_does_not_fail_the_run(tmp_path: Path) -> None:
    _make_recipe(tmp_path)
    received: list[bytes] = []
    server = _fake_server(500, received)
    try:
        proc = _run_with_webhook(tmp_path, f"http://127.0.0.1:{server.server_port}/hook")
    finally:
        server.shutdown()
    assert proc.returncode == 0, "웹훅 오류가 실행 결과를 실패로 만들면 안 된다"
    assert list((tmp_path / "out").glob("*.pptx"))
    # 접속할 수 없는 주소여도 실행은 성공해야 한다
    dead = _run_with_webhook(tmp_path, "http://127.0.0.1:9/hook")
    assert dead.returncode == 0
