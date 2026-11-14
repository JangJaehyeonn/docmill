"""`McpEngineTool.call_tool` 공개 메서드 단위 테스트."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from docmill.engines.base import DirectToolCaller, EngineResult
from docmill.engines.gridcraft import GridCraftTool
from docmill.engines.mcp_tool import _path_like_values
from docmill.tools.report import Group, _slide_commands, plan_slides


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


@pytest.mark.parametrize("kind", ["dotdot", "absolute", "symlink"])
@pytest.mark.parametrize("key", ["path", "output_path", "export"])
def test_call_tool_rejects_outside_paths_before_engine(
    tmp_path: Path, kind: str, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """작업 폴더 밖 경로는 `_run`을 부르기 전에 path-check로 거부한다."""
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    (work / "link.png").symlink_to(outside / "target.png")
    bad = {"dotdot": "../x.png", "absolute": str(outside / "x.png"), "symlink": "link.png"}[kind]
    calls: list[str] = []

    def fake_run(self: Any, tool: str, arguments: dict[str, Any]) -> EngineResult:
        calls.append(tool)
        return EngineResult(ok=True)

    monkeypatch.setattr(GridCraftTool, "_run", fake_run)
    with GridCraftTool(work) as grid:
        result = grid.call_tool("export_file", {"opts": {key: bad}})
    assert not result.ok
    assert result.details["step"] == "path-check"
    assert calls == []


def test_report_call_tool_arguments_stay_inside_workdir(tmp_path: Path) -> None:
    """report.py가 넘기는 인자 중 경로 힌트에 걸리는 값은 모두 작업 폴더 안이다.

    엔진 도구(new_presentation·add_slide·insert_chart·read_range)에는 파일 경로 인자가 없다.
    힌트에 걸리는 키(예: `layout`의 `out`)는 값이 단순 이름이라 검사를 통과해야 한다.
    """
    groups = [Group("A", 1.0, 50.0), Group("B", 1.0, 50.0)]
    args = {"group_by": "g", "value": "v", "slides": 6, "chart": "bar"}
    calls: list[dict[str, Any]] = [{}, {"blank": True, "theme": "x"}]
    for kind in sorted(set(plan_slides(6))):
        calls += [a for _tool, a in _slide_commands(kind, groups, args, 0)]
    with GridCraftTool(tmp_path) as grid:
        for call in calls:
            assert not isinstance(grid._check(*_path_like_values(call)), EngineResult)
        # no argument key besides known non-path names is path-like
        keys = {k for call in calls for k in call}
        assert keys <= {
            "blank", "theme", "layout", "title", "body", "type", "categories", "series",
        }  # fmt: skip


def test_mcp_engine_tool_satisfies_direct_tool_caller(tmp_path: Path) -> None:
    with GridCraftTool(tmp_path) as grid:
        assert isinstance(grid, DirectToolCaller)
