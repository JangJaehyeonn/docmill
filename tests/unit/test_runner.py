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


def test_bind_args_step_variable() -> None:
    bound = bind_args({"document": "$step1"}, {"$input": "a", "$step1": "made.pptx"}, "s")
    assert bound == {"document": "made.pptx"}


def test_unknown_variable_error_names_step_and_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save(tmp_path, "r", [{"tool": "verify_numbers", "args": {"source": "$input2"}}])
    out = json.loads(run_recipe("r", "x.xlsx"))
    assert out["step"] == "1:verify_numbers" and "$input2" in out["error"]


def test_saved_report_then_verify_session_replays_with_new_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docmill.recipes import SessionRecorder, make_save_recipe, recording

    built = tmp_path / "built.pptx"
    built.write_bytes(b"x")

    def excel_to_report_ppt(input: str) -> str:
        return json.dumps({"path": str(built), "summary": "one"})

    def verify_numbers(document: str, source: str) -> str:
        return json.dumps({"summary": "checked"})

    rec = SessionRecorder()
    recording(excel_to_report_ppt, rec)(input="/a.xlsx")
    recording(verify_numbers, rec)(document=str(built), source="/a.xlsx")
    steps = json.loads(make_save_recipe(rec)("r"))["steps"]
    assert steps[1]["args"] == {"document": "$step1", "source": "$input"}

    seen: list[dict[str, str]] = []

    def replay_verify(document: str, source: str) -> str:
        seen.append({"document": document, "source": source})
        return json.dumps({"path": document, "summary": "checked"})

    monkeypatch.setitem(runner.RUNNERS, "excel_to_report_ppt", excel_to_report_ppt)
    monkeypatch.setitem(runner.RUNNERS, "verify_numbers", replay_verify)
    out = json.loads(run_recipe("r", "/b.xlsx"))
    assert seen == [{"document": str(built), "source": "/b.xlsx"}]
    assert out["summary"] == "checked"


def test_deliver_leaves_no_partial_files_and_is_repeatable(tmp_path: Path) -> None:
    source = tmp_path / "s.pptx"
    source.write_bytes(b"abc")
    out_dir = tmp_path / "out"
    for _ in range(3):
        runner._deliver(str(source), str(out_dir), "r")
    assert [p.name for p in out_dir.iterdir()] == ["r.pptx"]
