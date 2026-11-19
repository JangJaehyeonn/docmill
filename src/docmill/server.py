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
from docmill.tools.compare import compare_versions
from docmill.tools.contradictions import find_contradictions
from docmill.tools.guard import guarded_edit
from docmill.tools.image import insert_image
from docmill.tools.mask import mask_text, unmask_text
from docmill.tools.official_style import check_official_style
from docmill.tools.preflight import preflight
from docmill.tools.refresh import refresh_links
from docmill.tools.rehearse import register_reviewer, rehearse_review
from docmill.tools.report import excel_to_report_ppt
from docmill.tools.review import review_map
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


def _register_guard(server: MCPServer) -> None:
    """`guarded_edit` 업무 도구(FR-31)를 등록한다."""
    server.tool()(guarded_edit)


def _register_review(server: MCPServer) -> None:
    """`review_map` 업무 도구(FR-33)를 등록한다."""
    server.tool()(review_map)


def _register_preflight(server: MCPServer) -> None:
    """`preflight` 업무 도구(FR-34)를 등록한다."""
    server.tool()(preflight)


def _register_refresh(server: MCPServer) -> None:
    """`refresh_links` 업무 도구(FR-37)를 등록한다."""
    server.tool()(refresh_links)


def _register_official_style(server: MCPServer) -> None:
    """`check_official_style` 업무 도구(FR-38)를 등록한다."""
    server.tool()(check_official_style)


def _register_mask(server: MCPServer) -> None:
    """`mask_text`·`unmask_text` 업무 도구(FR-39)를 등록한다."""
    server.tool()(mask_text)
    server.tool()(unmask_text)


def _register_contradictions(server: MCPServer) -> None:
    """`find_contradictions` 업무 도구(FR-40)를 등록한다."""
    server.tool()(find_contradictions)


def _register_compare(server: MCPServer) -> None:
    """`compare_versions` 업무 도구(FR-41)를 등록한다."""
    server.tool()(compare_versions)


def _register_image(server: MCPServer) -> None:
    """`insert_image` 업무 도구(FR-42)를 등록한다."""
    server.tool()(insert_image)


def _register_rehearse(server: MCPServer) -> None:
    """`register_reviewer`·`rehearse_review` 업무 도구(FR-43)를 등록한다."""
    server.tool()(register_reviewer)
    server.tool()(rehearse_review)


TOOL_REGISTRARS: tuple[ToolRegistrar, ...] = (
    _register_ping,
    _register_report,
    _register_tasks,
    _register_template,
    _register_verify,
    _register_suggest,
    _register_guard,
    _register_review,
    _register_preflight,
    _register_refresh,
    _register_official_style,
    _register_mask,
    _register_contradictions,
    _register_compare,
    _register_image,
    _register_rehearse,
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
