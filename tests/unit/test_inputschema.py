"""레시피 입력 변화 감지 단위 테스트 (FR-22)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from docmill import recipes, runner
from docmill.cli import app
from docmill.inputschema import (
    SchemaError,
    active_column_map,
    apply_map_to_columns,
    column_type,
    map_header,
    parse_column_map,
    schema_from_rows,
    suggest_mapping,
    using_column_map,
)
from docmill.recipes import RecipeError, SessionRecorder, make_save_recipe, recording
from docmill.runner import InputChangedError, check_input_schema, run_recipe

ROWS: list[list[Any]] = [["부서", "월", "매출"], ["영업", "7월", 10.0], ["개발", "7월", 5]]
SAVED = schema_from_rows(["매출"], ROWS)
STEPS = [
    {
        "tool": "excel_to_report_ppt",
        "args": {"input": "$input", "group_by": "부서", "value": "매출"},
    }
]
cli = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))


def _renamed(header: list[str]) -> dict[str, Any]:
    return schema_from_rows(["금액"], [header, *ROWS[1:]])


def test_column_type_labels() -> None:
    assert column_type([1, 2.5, None, ""]) == "number"
    assert column_type(["a", None]) == "text"
    assert column_type([True]) == "boolean"
    assert column_type([1, "a"]) == "mixed"
    assert column_type([None, " "]) == "empty"


def test_schema_from_rows_records_sheets_columns_and_types() -> None:
    assert SAVED == {
        "sheets": ["매출"],
        "columns": ["부서", "월", "매출"],
        "column_types": {"부서": "text", "월": "text", "매출": "number"},
    }


def test_parse_column_map() -> None:
    assert parse_column_map([" 금액 = 매출 ", "팀=부서"]) == {"금액": "매출", "팀": "부서"}
    for bad in (["금액"], ["=매출"], ["a=b", "a=c"], ["a=x", "b=x"]):
        with pytest.raises(SchemaError):
            parse_column_map(bad)


def test_apply_map_to_columns() -> None:
    assert apply_map_to_columns(["부서", "금액"], {"금액": "매출"}) == ["부서", "매출"]
    with pytest.raises(SchemaError, match="없다"):
        apply_map_to_columns(["부서"], {"금액": "매출"})
    with pytest.raises(SchemaError, match="같은 이름"):
        apply_map_to_columns(["부서", "금액"], {"금액": "부서"})


def test_map_header_only_inside_context() -> None:
    rows: list[list[Any]] = [["부서", "금액"], ["a", 1]]
    assert map_header(rows) == rows
    with using_column_map({"금액": "매출"}):
        assert active_column_map() == {"금액": "매출"}
        assert map_header(rows) == [["부서", "매출"], ["a", 1]]
    assert active_column_map() == {}


def test_suggestion_for_renamed_column_has_reason() -> None:
    suggestions = suggest_mapping(SAVED, _renamed(["부서", "월", "금액"]))
    assert suggestions == [
        {
            "changed": "금액",
            "original": "매출",
            "reason": "같은 위치(3번째 열), 같은 형식(숫자)",
            "map": "금액=매출",
        }
    ]


def test_suggestion_prefers_position_and_type_and_is_deterministic() -> None:
    header = ["부서", "매출액", "월", "금액"]
    actual = schema_from_rows(["s"], [header, ["영업", "x", "7월", 1.0], ["개발", "y", "8월", 2]])
    saved = schema_from_rows(["s"], [["부서", "매출", "월", "비고"], ["a", 1.0, "7월", "z"]])
    first = suggest_mapping(saved, actual)
    assert first == suggest_mapping(saved, actual)
    by_original = {s["original"]: s for s in first}
    # 매출(number, 2nd) -> 매출액 is same position + similar name (text, so no type match)
    assert by_original["매출"]["changed"] == "매출액"
    assert "비슷한 이름" in by_original["매출"]["reason"]
    assert by_original["비고"]["changed"] == "금액"


def test_no_suggestion_without_any_rule_match() -> None:
    saved = schema_from_rows(["s"], [["k", "a"], ["t", 1]])
    actual = schema_from_rows(["s"], [["zzz", "k"], ["t", "u"]])
    assert suggest_mapping(saved, actual) == []


def _fake_schema(monkeypatch: pytest.MonkeyPatch, schema: dict[str, Any]) -> None:
    monkeypatch.setattr(runner, "read_input_schema", lambda _path: schema)


def test_check_input_schema_raises_with_suggestions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "in.xlsx"
    src.write_bytes(b"x")
    _fake_schema(monkeypatch, _renamed(["부서", "월", "금액"]))
    with pytest.raises(InputChangedError) as info:
        check_input_schema(STEPS, SAVED, str(src))
    assert info.value.missing == ["매출"]
    assert info.value.suggestions[0]["changed"] == "금액"
    with using_column_map({"금액": "매출"}):
        assert check_input_schema(STEPS, SAVED, str(src)) is True


def test_check_input_schema_skips_without_schema_or_excel(tmp_path: Path) -> None:
    assert check_input_schema(STEPS, None, str(tmp_path / "a.xlsx")) is False
    assert check_input_schema(STEPS, SAVED, str(tmp_path / "a.docx")) is False


def test_check_input_schema_bad_map_is_recipe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "in.xlsx"
    src.write_bytes(b"x")
    _fake_schema(monkeypatch, SAVED)
    with using_column_map({"없음": "매출"}), pytest.raises(RecipeError) as info:
        check_input_schema(STEPS, SAVED, str(src))
    assert info.value.step == "input_schema"


def _save_recipe(home: Path) -> None:
    folder = home / "recipes"
    folder.mkdir(parents=True, exist_ok=True)
    data = {"name": "r", "steps": STEPS, "input_schema": SAVED}
    (folder / "r.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_run_recipe_stops_before_steps_on_changed_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_recipe(tmp_path)
    src = tmp_path / "in.xlsx"
    src.write_bytes(b"x")
    _fake_schema(monkeypatch, _renamed(["부서", "월", "금액"]))
    called: list[str] = []
    monkeypatch.setitem(runner.RUNNERS, "excel_to_report_ppt", lambda **kw: called.append("x"))
    out = json.loads(run_recipe("r", str(src), str(tmp_path / "out")))
    assert out["step"] == "input_schema" and out["missing"] == ["매출"]
    assert out["suggestions"][0]["original"] == "매출"
    assert called == [] and not (tmp_path / "out").exists()


def test_cli_run_prints_suggestions_and_rejects_bad_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_recipe(tmp_path)
    src = tmp_path / "in.xlsx"
    src.write_bytes(b"x")
    _fake_schema(monkeypatch, _renamed(["부서", "월", "금액"]))
    monkeypatch.setattr("docmill.webhook.send_summary", lambda summary: None)
    result = cli.invoke(app, ["run", "r", "--input", str(src)])
    assert result.exit_code == 1
    body = result.stdout[: result.stdout.rindex("}") + 1]
    assert json.loads(body)["suggestions"][0]["map"] == "금액=매출"
    bad = cli.invoke(app, ["run", "r", "--input", str(src), "--map", "금액"])
    assert bad.exit_code == 2


def test_save_recipe_stores_input_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "sales.xlsx"
    src.write_bytes(b"x")
    monkeypatch.setattr(recipes, "read_input_schema", lambda _path: SAVED)

    def excel_to_report_ppt(input: str, group_by: str, value: str) -> str:
        return json.dumps({"path": "made.pptx"})

    rec = SessionRecorder()
    recording(excel_to_report_ppt, rec)(str(src), "부서", "매출")
    out = json.loads(make_save_recipe(rec)("r"))
    assert out["input_schema"] == SAVED
    shown = cli.invoke(app, ["recipes", "show", "r", "--json"])
    assert shown.exit_code == 0
    stored = json.loads(shown.stdout)
    assert stored["input_schema"] == SAVED and str(tmp_path) not in shown.stdout
    text = cli.invoke(app, ["recipes", "show", "r"])
    assert "매출(number)" in text.stdout


def test_save_recipe_schema_failure_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "sales.xlsx"
    src.write_bytes(b"x")

    def boom(_path: Path) -> dict[str, Any]:
        raise SchemaError("engine down")

    monkeypatch.setattr(recipes, "read_input_schema", boom)

    def excel_to_report_ppt(input: str) -> str:
        return json.dumps({"path": "made.pptx"})

    rec = SessionRecorder()
    recording(excel_to_report_ppt, rec)(str(src))
    out = json.loads(make_save_recipe(rec)("r"))
    assert out["step"] == "input_schema"


def test_show_missing_recipe_fails() -> None:
    assert cli.invoke(app, ["recipes", "show", "none", "--json"]).exit_code == 1
