import json
from pathlib import Path

import pytest

from docmill.recipes import SessionRecorder, build_steps, make_save_recipe, recording


def excel_to_report_ppt(input: str, out_dir: str | None = None) -> str:
    return json.dumps({"path": "x"})


def failing(input: str) -> str:
    return json.dumps({"error": "boom"})


failing.__name__ = "excel_to_report_ppt"


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))


def test_save_returns_steps_with_variable_and_writes_file(tmp_path: Path) -> None:
    rec = SessionRecorder()
    recording(excel_to_report_ppt, rec)("/data/sales.xlsx", out_dir="/tmp/o")
    out = json.loads(make_save_recipe(rec)("주간"))
    assert out["steps"] == [{"tool": "excel_to_report_ppt", "args": {"input": "$input"}}]
    assert "sales.xlsx" not in json.dumps(out["steps"])
    saved = json.loads((tmp_path / "recipes" / "주간.json").read_text(encoding="utf-8"))
    assert saved["steps"] == out["steps"]


def test_failed_calls_are_excluded() -> None:
    rec = SessionRecorder()
    recording(excel_to_report_ppt, rec)(input="/a.xlsx")
    recording(failing, rec)(input="/b.xlsx")
    out = json.loads(make_save_recipe(rec)("r"))
    assert len(out["steps"]) == 1


def test_empty_session_and_bad_name_are_errors() -> None:
    assert json.loads(make_save_recipe(SessionRecorder())("r"))["step"] == "steps"
    rec = SessionRecorder()
    recording(excel_to_report_ppt, rec)(input="/a.xlsx")
    assert json.loads(make_save_recipe(rec)("../x"))["step"] == "name"


def test_distinct_paths_get_numbered_variables() -> None:
    steps = build_steps(
        [
            ("excel_to_report_ppt", {"input": "/a.xlsx"}),
            ("excel_to_report_ppt", {"input": "/b.xlsx"}),
            ("excel_to_report_ppt", {"input": "/a.xlsx"}),
        ]
    )
    assert [s["args"]["input"] for s in steps] == ["$input", "$input2", "$input"]


def test_same_file_under_different_arg_names_shares_one_variable() -> None:
    steps = build_steps(
        [("verify_numbers", {"document": "/a.xlsx", "source": "/a.xlsx"})],
    )
    assert steps[0]["args"] == {"document": "$input", "source": "$input"}


def test_step_output_becomes_step_variable() -> None:
    steps = build_steps(
        [
            ("excel_to_report_ppt", {"input": "/a.xlsx"}),
            ("verify_numbers", {"document": "/o.pptx", "source": "/a.xlsx"}),
        ],
        ["/o.pptx", None],
    )
    assert steps[1]["args"] == {"document": "$step1", "source": "$input"}
