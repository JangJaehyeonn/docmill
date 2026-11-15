"""run_recipe 단위 테스트 (FR-10)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docmill import runner
from docmill.recipes import RecipeError
from docmill.runner import bind_args, run_recipe


def _save(home: Path, name: str, steps: list[dict[str, Any]]) -> None:
    folder = home / "recipes"
    folder.mkdir(parents=True)
    (folder / f"{name}.json").write_text(json.dumps({"name": name, "steps": steps}))


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))


def test_bind_args_replaces_variables() -> None:
    bound = bind_args({"input": "$input", "n": 1}, {"$input": "a.xlsx"}, "s")
    assert bound == {"input": "a.xlsx", "n": 1}


def test_bind_args_unknown_variable() -> None:
    with pytest.raises(RecipeError):
        bind_args({"input": "$other"}, {"$input": "a"}, "s")


def test_missing_recipe_returns_error() -> None:
    out = json.loads(run_recipe("none", "a.xlsx"))
    assert out["step"] == "recipe" and out["run_id"]


def test_runs_steps_and_copies_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    built = tmp_path / "built.pptx"
    built.write_bytes(b"x")
    seen: list[str] = []

    def fake(input: str) -> str:
        seen.append(input)
        return json.dumps({"path": str(built), "summary": "ok"})

    monkeypatch.setitem(runner.RUNNERS, "excel_to_report_ppt", fake)
    _save(tmp_path, "r", [{"tool": "excel_to_report_ppt", "args": {"input": "$input"}}])
    out = json.loads(run_recipe("r", "new.xlsx", str(tmp_path / "out")))
    assert seen == ["new.xlsx"]
    assert out["path"] == str(tmp_path / "out" / "r.pptx")
    assert Path(out["path"]).read_bytes() == b"x"
    assert out["ai_used"] is False


def test_failed_step_stops_with_step_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(input: str) -> str:
        return json.dumps({"error": "boom", "step": "columns"})

    monkeypatch.setitem(runner.RUNNERS, "excel_to_report_ppt", fail)
    _save(tmp_path, "r", [{"tool": "excel_to_report_ppt", "args": {"input": "$input"}}])
    out = json.loads(run_recipe("r", "x.xlsx"))
    assert out["error"] == "boom" and out["step"] == "1:excel_to_report_ppt(columns)"


def test_unknown_tool_in_recipe(tmp_path: Path) -> None:
    _save(tmp_path, "r", [{"tool": "rm_rf", "args": {}}])
    assert json.loads(run_recipe("r", "x"))["step"] == "1:rm_rf"


def test_bind_args_uses_previous_output_for_other_variables() -> None:
    bound = bind_args({"input": "$input2"}, {"$input": "a.xlsx"}, "s", previous="made.pptx")
    assert bound == {"input": "made.pptx"}


def test_chained_steps_pass_previous_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = tmp_path / "built.pptx"
    built.write_bytes(b"x")
    seen: list[str] = []

    def first(input: str) -> str:
        return json.dumps({"path": str(built), "summary": "one"})

    def second(input: str, source: str) -> str:
        seen.append(source)
        return json.dumps({"path": str(built), "summary": "two"})

    monkeypatch.setitem(runner.RUNNERS, "excel_to_report_ppt", first)
    monkeypatch.setitem(runner.RUNNERS, "apply_template", second)
    steps = [
        {"tool": "excel_to_report_ppt", "args": {"input": "$input"}},
        {"tool": "apply_template", "args": {"input": "$input", "source": "$source"}},
    ]
    _save(tmp_path, "r", steps)
    out = json.loads(run_recipe("r", "new.xlsx"))
    assert seen == [str(built)] and out["summary"] == "two"


def test_deliver_leaves_no_partial_files_and_is_repeatable(tmp_path: Path) -> None:
    source = tmp_path / "s.pptx"
    source.write_bytes(b"abc")
    out_dir = tmp_path / "out"
    for _ in range(3):
        runner._deliver(str(source), str(out_dir), "r")
    assert [p.name for p in out_dir.iterdir()] == ["r.pptx"]
