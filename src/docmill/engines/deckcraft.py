"""DeckCraft(pptx) 엔진 어댑터."""

from __future__ import annotations

from pathlib import Path

from docmill.engines.base import EngineResult
from docmill.engines.mcp_tool import McpEngineTool


class DeckCraftTool(McpEngineTool):
    """`deckcraft-cli mcp`를 작업 폴더 read/write root와 함께 호출한다."""

    name = "deckcraft"
    extensions = (".pptx",)
    env_var = "DOCMILL_DECKCRAFT_PATH"
    binary = "deckcraft-cli"
    confine_with_roots = True
    open_tool = "open_presentation"
    save_tool = "save_presentation"
    execute_tool = "run_command"

    def _render_opened(self, source: Path, out_dir: Path) -> EngineResult:
        target = out_dir / f"{source.stem}.png"
        return self._run("export", {"path": str(target), "format": "png", "all": True})
