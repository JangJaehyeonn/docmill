"""WordCraft(docx) 엔진 어댑터."""

from __future__ import annotations

from pathlib import Path

from docmill.engines.base import EngineResult
from docmill.engines.mcp_tool import McpEngineTool


class WordCraftTool(McpEngineTool):
    """`wordcraft-cli mcp`를 작업 폴더 read/write root와 함께 호출한다."""

    name = "wordcraft"
    extensions = (".docx",)
    env_var = "DOCMILL_WORDCRAFT_PATH"
    binary = "wordcraft-cli"
    confine_with_roots = True
    open_tool = "open_document"
    save_tool = "save_document"
    execute_tool = "execute"

    def _render_opened(self, source: Path, out_dir: Path) -> EngineResult:
        result = self._run("render_page", {"page": 1})
        images = result.details.get("images") or []
        if not result.ok or not images:
            return (
                result
                if not result.ok
                else EngineResult(ok=False, message="wordcraft 엔진이 이미지를 돌려주지 않았다")
            )
        checked = self._check(out_dir / f"{source.stem}.png")  # resolves symlinks
        if isinstance(checked, EngineResult):
            return checked
        target = checked[0]
        try:
            target.write_bytes(images[0])
        except OSError as exc:
            return EngineResult(
                ok=False,
                message=f"wordcraft 이미지 저장 실패 ({target}): {exc}",
                details={"step": "render_page"},
            )
        return EngineResult(ok=True, output=str(target), details={"step": "render_page"})
