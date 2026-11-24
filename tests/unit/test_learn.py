import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from docmill.cli import app
from docmill.learn import Call, accept, call_logger, find_repeats, record_call, suggest
from docmill.recipes import RecipeError, SessionRecorder, recipes_dir, recording, show_recipe
from docmill.runlog import DB_NAME, get_home

runner = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))


def _report(path: str, slides: int = 5) -> Call:
    return ("excel_to_report_ppt", {"input": path, "slides": slides}, f"{path}.pptx")


def _verify(path: str) -> Call:
    return ("verify_numbers", {"source": "ref.xlsx", "document": path}, None)


def test_three_repeats_suggest_two_do_not() -> None:
    assert find_repeats([[_report("a.xlsx"), _report("b.xlsx")]]) == []
    found = find_repeats([[_report("a.xlsx"), _report("b.xlsx"), _report("c.xlsx")]])
    assert len(found) == 1
    assert found[0].count == 3
    assert found[0].steps == [
        {"tool": "excel_to_report_ppt", "args": {"input": "$input", "slides": 5}}
    ]
    assert "레시피로 저장할까요" in found[0].to_dict()["message"]


def test_other_args_must_match() -> None:
    calls = [_report("a.xlsx"), _report("b.xlsx", slides=3), _report("c.xlsx")]
    assert find_repeats([calls]) == []


def test_repeats_across_sessions_count() -> None:
    sessions = [[_report(f"{n}.xlsx")] for n in "abc"]
    assert [s.count for s in find_repeats(sessions)] == [3]


def test_multi_step_sequence_is_suggested_once() -> None:
    session: list[Call] = []
    for n in "abc":
        session += [_report(f"{n}.xlsx"), _verify(f"{n}.xlsx.pptx")]
    found = find_repeats([session])
    assert len(found) == 1
    assert [step["tool"] for step in found[0].steps] == ["excel_to_report_ppt", "verify_numbers"]
    assert found[0].steps[1]["args"]["document"] == "$step1"


def test_accepted_pattern_is_not_suggested_again() -> None:
    for n in "abc":
        record_call("s1", *_report(str(Path(f"/data/{n}.xlsx"))))
    assert len(suggest()) == 1
    saved = accept("반복")
    assert saved["steps"][0]["args"]["input"] == "$input"
    assert show_recipe("반복")["steps"] == saved["steps"]
    assert suggest() == []
    with pytest.raises(RecipeError):
        accept("또")


def test_accept_rejects_bad_pick() -> None:
    for n in "abc":
        record_call("s1", *_report(f"{n}.xlsx"))
    with pytest.raises(RecipeError):
        accept("x", pick=2)


def test_recorder_hook_persists_calls() -> None:
    recorder = SessionRecorder(on_add=call_logger("s1"))
    for n in "abc":
        tool, args, out = _report(f"{n}.xlsx")
        recorder.add(tool, args, out)
    assert len(recorder.calls) == 3
    assert [s.count for s in suggest()] == [3]


def test_cli_suggest_and_accept() -> None:
    empty = runner.invoke(app, ["recipes", "suggest"])
    assert empty.exit_code == 0
    assert json.loads(empty.stdout) == {"suggestions": []}
    for n in "abc":
        record_call("s1", *_report(f"/data/{n}.xlsx"))
    shown: dict[str, Any] = json.loads(runner.invoke(app, ["recipes", "suggest"]).stdout)
    assert shown["suggestions"][0]["count"] == 3
    done = runner.invoke(app, ["recipes", "suggest", "--accept", "주간"])
    assert done.exit_code == 0, done.output
    assert (recipes_dir() / "주간.json").is_file()
    again = runner.invoke(app, ["recipes", "suggest", "--accept", "주간2"])
    assert again.exit_code == 1


def test_same_tools_with_other_args_are_not_covered() -> None:
    names = ["a", "b", "c", "d", "e", "f"]
    calls = [_report(n + ".xlsx", 5 if i < 3 else 3) for i, n in enumerate(names)]
    # slides=5 report x3 is also a prefix of a longer report->verify task with slides=3
    longer = [c for n in "xyz" for c in (_report(n + ".xlsx", 3), _verify(n + ".pptx"))]
    found = find_repeats([calls[:3], longer])
    slides = [s.steps[0]["args"]["slides"] for s in found if len(s.steps) == 1]
    assert 5 in slides


def test_accept_rejects_pattern_with_two_inputs() -> None:
    for n in ["a", "b", "c"]:
        record_call("s", "excel_to_report_ppt", {"input": n + ".xlsx"}, n + ".pptx")
        record_call("s", "verify_numbers", {"source": "ref.xlsx", "document": n + ".pptx"}, None)
    assert suggest()
    with pytest.raises(RecipeError):
        accept("two-inputs")


def test_guarded_edit_is_logged_but_not_in_recipe() -> None:
    recorder = SessionRecorder(on_add=call_logger("s1"))
    recorder.log_only("guarded_edit", {"input": "a.docx"}, "o.docx")
    assert recorder.calls == []
    con = sqlite3.connect(get_home(None) / DB_NAME)
    rows = con.execute("SELECT tool FROM session_calls").fetchall()
    con.close()
    assert rows == [("guarded_edit",)]


def test_recording_logs_successful_guarded_edit_only() -> None:
    recorder = SessionRecorder(on_add=call_logger("s1"))

    def guarded_edit(input: str, fail: bool = False) -> str:
        return json.dumps({"error": "x"} if fail else {"path": "o.docx"})

    wrapped = recording(guarded_edit, recorder)
    wrapped("a.docx", fail=True)
    wrapped("b.docx")
    con = sqlite3.connect(get_home(None) / DB_NAME)
    rows = con.execute("SELECT tool, args_json FROM session_calls").fetchall()
    con.close()
    assert [r[0] for r in rows] == ["guarded_edit"]
    assert "b.docx" in rows[0][1]
    assert recorder.calls == []


def test_edit_breaks_the_sequence() -> None:
    recorder = SessionRecorder(on_add=call_logger("s1"))
    for n in ("a", "b", "c"):
        tool, args, out = _report(f"{n}.xlsx")
        recorder.add(tool, args, out)
        recorder.log_only("guarded_edit", {"input": out}, "o")
        recorder.add(*_verify(f"{n}.pptx")[:2], None)
    assert all(len(s.steps) == 1 for s in suggest())
