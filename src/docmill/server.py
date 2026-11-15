"""docmill MCP 서버.

AI 앱에는 업무 단위 도구만 노출한다. 엔진의 저수준 도구는 서버 내부에서만 쓴다.
지금은 연결 확인용 `ping` 도구만 있고, 업무 도구는 FR-05 ~ FR-10에서 추가한다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from docmill import __version__
from docmill.runlog import logged
from docmill.tools.report import excel_to_report_ppt

SERVER_NAME = "docmill"

ToolRegistrar = Callable[[MCPServer], None]


def _register_ping(server: MCPServer) -> None:
    """연결 확인용 `ping` 도구를 등록한다."""

    @server.tool()
    def ping() -> str:
        """서버 연결을 확인한다. 연결되면 docmill 버전을 돌려준다."""
        return f"docmill {__version__} 연결됨"


# 업무 도구를 추가할 때 등록 함수를 이 목록에 넣으면 같은 서버에 노출된다.
def _register_report(server: MCPServer) -> None:
    """`excel_to_report_ppt` 업무 도구(FR-05)를 등록한다."""
    server.tool()(excel_to_report_ppt)


TOOL_REGISTRARS: tuple[ToolRegistrar, ...] = (_register_ping, _register_report)


def _log_every_tool(server: MCPServer) -> None:
    """서버에 등록되는 모든 도구를 실행 로그(FR-16)로 감싼다."""
    add_tool = server.add_tool

    def add_logged_tool(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return add_tool(logged(fn), *args, **kwargs)

    server.add_tool = add_logged_tool  # type: ignore[method-assign]


def build_server() -> MCPServer:
    """도구가 등록된 MCP 서버를 만든다."""
    server = MCPServer(SERVER_NAME)
    _log_every_tool(server)
    for register in TOOL_REGISTRARS:
        register(server)
    return server
