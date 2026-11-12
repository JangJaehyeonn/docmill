"""FR-01 MCP 서버 실행 인수 테스트 (초안).

필요한 샘플: 없음.
가정: `python -m docmill.cli mcp`가 `docmill mcp`와 같은 stdio 서버를 띄운다.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parents[3]
BUSINESS_TOOLS = {
    "excel_to_report_ppt",
    "doc_to_task_table",
    "apply_template",
    "verify_numbers",
    "save_recipe",
    "run_recipe",
}
# 엔진의 저수준 도구 이름 (Phase 0 기록 기준)
ENGINE_LOW_LEVEL_TOOLS = {
    "new_workbook",
    "open_workbook",
    "read_range",
    "write_range",
    "save_workbook",
    "evaluate_formula",
    "format_range",
    "new_presentation",
    "open_presentation",
    "add_slide",
    "insert_chart",
    "insert_table",
    "set_text",
    "render_slide",
    "save_presentation",
    "new_document",
    "open_document",
    "type_text",
    "get_text",
    "save_document",
    "render_page",
    "execute_command",
    "run_command",
    "inspect_slide",
    "inspect_document",
    "inspect_workbook",
}


async def _list_tool_names(tmp_path: Path) -> set[str]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "docmill.cli", "mcp"],
        cwd=str(tmp_path),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.list_tools()
        return {tool.name for tool in result.tools}


@pytest.mark.fr("FR-01")
def test_mcp_server_starts_and_lists_tools(tmp_path: Path) -> None:
    names = asyncio.run(asyncio.wait_for(_list_tool_names(tmp_path), timeout=30))
    assert names, "도구 목록이 비어 있다"


@pytest.mark.fr("FR-01")
def test_engine_low_level_tools_are_not_exposed(tmp_path: Path) -> None:
    names = asyncio.run(asyncio.wait_for(_list_tool_names(tmp_path), timeout=30))
    assert not (names & ENGINE_LOW_LEVEL_TOOLS)
    # 연결 확인용 ping 외에는 업무 도구 6개 안에서만 노출된다
    assert names <= BUSINESS_TOOLS | {"ping"}


@pytest.mark.fr("FR-01")
def test_readme_documents_claude_connection() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "claude mcp add" in readme, "Claude Code 연결 방법이 없다"
    assert "claude_desktop_config" in readme or "Claude 데스크톱" in readme, (
        "Claude 데스크톱 연결 방법이 없다"
    )
    assert "docmill mcp" in readme
