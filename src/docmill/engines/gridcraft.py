"""GridCraft(xlsx) 엔진 어댑터."""

from __future__ import annotations

from pathlib import Path

from docmill.engines.base import EngineResult
from docmill.engines.mcp_tool import McpEngineTool


class GridCraftTool(McpEngineTool):
    """`gridcraft-cli mcp`를 호출한다."""

    name = "gridcraft"
    extensions = (".xlsx",)
    env_var = "DOCMILL_GRIDCRAFT_PATH"
    binary = "gridcraft-cli"
    open_tool = "open_workbook"
    save_tool = "save_workbook"
    execute_tool = "execute_command"

    def render(self, path: Path, out_dir: Path) -> EngineResult:
        """헤드리스 GridCraft는 미리보기를 만들 수 없다. 경로 검사 후 실패를 돌려준다."""
        checked = self._check(path, out_dir)
        if isinstance(checked, EngineResult):
            return checked
        return EngineResult(
            ok=False,
            message="gridcraft 엔진은 헤드리스 모드에서 미리보기(PNG)를 지원하지 않는다",
            details={"step": "render"},
        )
