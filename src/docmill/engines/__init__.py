"""문서 엔진(GridCraft·DeckCraft·WordCraft) 어댑터."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docmill.engines.mcp_tool import McpEngineTool

DEFAULT_TIMEOUT_SECONDS = 60
"""엔진 프로세스 한 번의 호출(시작 포함)에 허용하는 기본 시간(초)."""


def get_tool(
    path: Path | str, workdir: Path | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> McpEngineTool:
    """확장자(대소문자 무시)로 알맞은 엔진 어댑터를 골라 돌려준다.

    `workdir`를 주지 않으면 작업별 임시 폴더를 만들고, 어댑터를 닫을 때 지운다.
    지원하지 않는 확장자는 `ValueError`.
    """
    suffix = Path(path).suffix.lower()
    for cls in _adapter_classes():
        if suffix in cls.extensions:
            if workdir is not None:
                return cls(workdir, timeout)
            temp = Path(tempfile.mkdtemp(prefix="docmill-"))
            try:
                tool = cls(temp, timeout)
            except BaseException:
                shutil.rmtree(temp, ignore_errors=True)
                raise
            tool.owns_workdir = True
            return tool
    supported = ", ".join(ext for cls in _adapter_classes() for ext in cls.extensions)
    raise ValueError(f"지원하지 않는 확장자: {suffix or '(없음)'} (지원: {supported})")


def _adapter_classes() -> list[type[McpEngineTool]]:
    from docmill.engines.deckcraft import DeckCraftTool
    from docmill.engines.gridcraft import GridCraftTool
    from docmill.engines.wordcraft import WordCraftTool

    return [GridCraftTool, DeckCraftTool, WordCraftTool]
