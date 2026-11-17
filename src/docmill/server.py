"""docmill MCP 서버.

AI 앱에는 업무 단위 도구만 노출한다. 엔진의 저수준 도구는 서버 내부에서만 쓴다.
지금은 연결 확인용 `ping` 도구만 있고, 업무 도구는 FR-05 ~ FR-10에서 추가한다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from docmill import __version__
from docmill.recipes import SessionRecorder, make_save_recipe, recording
from docmill.runlog import logged
from docmill.runner import run_recipe
from docmill.tools.report import excel_to_report_ppt
from docmill.tools.suggest import suggest_actions
from docmill.tools.tasks import doc_to_task_table
from docmill.tools.template import apply_template
from docmill.tools.verify import verify_numbers

SERVER_NAME = "docmill"
# 레시피 실행은 AI를 호출하지 않으므로 로그의 AI 사용 여부를 False로 남긴다 (FR-10).
AI_FREE_TOOL = "run_recipe"

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


def _register_tasks(server: MCPServer) -> None:
    """`doc_to_task_table` 업무 도구(FR-06)를 등록한다."""
    server.tool()(doc_to_task_table)


def _register_verify(server: MCPServer) -> None:
    """`verify_numbers` 업무 도구(FR-08)를 등록한다."""
    server.tool()(verify_numbers)


def _register_template(server: MCPServer) -> None:
    """`apply_template` 업무 도구(FR-07)를 등록한다."""
    server.tool()(apply_template)


def _register_suggest(server: MCPServer) -> None:
    """`suggest_actions` 업무 도구(FR-30)를 등록한다."""
    server.tool()(suggest_actions)


TOOL_REGISTRARS: tuple[ToolRegistrar, ...] = (
    _register_ping,
    _register_report,
    _register_tasks,
    _register_template,
    _register_verify,
    _register_suggest,
)


def _log_every_tool(server: MCPServer, recorder: SessionRecorder) -> None:
    """등록되는 모든 도구를 실행 로그(FR-16)로 감싸고, 성공한 호출은 세션에 기록한다(FR-09)."""
    add_tool = server.add_tool

    def add_logged_tool(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        wrapped = logged(recording(fn, recorder), ai_used=fn.__name__ != AI_FREE_TOOL)
        return add_tool(wrapped, *args, **kwargs)

    server.add_tool = add_logged_tool  # type: ignore[method-assign]


def build_server() -> MCPServer:
    """도구가 등록된 MCP 서버를 만든다."""
    server = MCPServer(SERVER_NAME)
    recorder = SessionRecorder()
    _log_every_tool(server, recorder)
    for register in TOOL_REGISTRARS:
        register(server)
    server.tool()(make_save_recipe(recorder))
    server.tool()(run_recipe)
    return server
