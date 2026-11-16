"""FR-19 원격 MCP 모드 인수 테스트 (초안).

필요한 샘플: 없음.
가정(구현 방법이 정해지면 맞춰 고친다):
- `docmill mcp --http --port N`이 127.0.0.1:N에서 streamable HTTP MCP 서버를 띄운다.
- MCP 엔드포인트 경로는 `/mcp`다.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

BUSINESS_TOOLS = {
    "excel_to_report_ppt",
    "doc_to_task_table",
    "apply_template",
    "verify_numbers",
    "save_recipe",
    "run_recipe",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_port(port: int, proc: subprocess.Popen[bytes], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert proc.poll() is None, "HTTP 서버가 바로 종료됐다"
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise AssertionError("HTTP 서버가 열리지 않았다")


@pytest.fixture
def http_server(tmp_path: Path) -> Iterator[int]:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "docmill.cli", "mcp", "--http", "--port", str(port)],
        env={**os.environ, "DOCMILL_HOME": str(tmp_path)},
        cwd=str(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_port(port, proc)
        yield port
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def _list_tool_names(port: int) -> set[str]:
    url = f"http://127.0.0.1:{port}/mcp"
    async with (
        streamable_http_client(url) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.list_tools()
        return {tool.name for tool in result.tools}


@pytest.mark.fr("FR-19")
def test_remote_client_can_list_tools_over_http(http_server: int) -> None:
    names = asyncio.run(asyncio.wait_for(_list_tool_names(http_server), timeout=30))
    assert names >= BUSINESS_TOOLS
