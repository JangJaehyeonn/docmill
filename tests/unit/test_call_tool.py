"""`McpEngineTool.call_tool` 공개 메서드 단위 테스트."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docmill.engines.base import EngineResult
from docmill.engines.gridcraft import GridCraftTool


def test_call_tool_delegates_to_run(tmp_path: Path, monkeypatch: Any) -> None:
    """call_tool은 도구 이름과 인자를 `_run`에 그대로 넘기고 결과를 돌려준다."""
    seen: list[tuple[str, dict[str, Any]]] = []
    expected = EngineResult(ok=True, output="{}")

    def fake_run(self: Any, tool: str, arguments: dict[str, Any]) -> EngineResult:
        seen.append((tool, arguments))
        return expected

    monkeypatch.setattr(GridCraftTool, "_run", fake_run)
    with GridCraftTool(tmp_path) as grid:
        result = grid.call_tool("read_range", {"a": 1})
    assert result is expected
    assert seen == [("read_range", {"a": 1})]
