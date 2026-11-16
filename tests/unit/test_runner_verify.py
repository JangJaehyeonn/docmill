"""FR-12 레시피 자동 검증 단위 테스트."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docmill.recipes import RecipeError
from docmill.runner import FORCE_VERIFY_FAIL_ENV, run_recipe, verify_outputs
from docmill.tools.report import excel_to_report_ppt

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
STEPS: list[dict[str, Any]] = [
    {
        "tool": "excel_to_report_ppt",
        "args": {"input": "$input", "group_by": "부서", "value": "매출"},
    }
]


def _save(home: Path) -> None:
    folder = home / "recipes"
    folder.mkdir(parents=True)
    (folder / "r.json").write_text(json.dumps({"steps": STEPS}), encoding="utf-8")


def test_passes_and_delivers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    monkeypatch.delenv(FORCE_VERIFY_FAIL_ENV, raising=False)
    _save(tmp_path)
    out = tmp_path / "out"
    result = json.loads(run_recipe("r", str(FIXTURES / "sales_week1.xlsx"), str(out)))
    assert "error" not in result, result
    assert list(out.glob("*.pptx"))


def test_forced_failure_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    monkeypatch.setenv(FORCE_VERIFY_FAIL_ENV, "1")
    _save(tmp_path)
    out = tmp_path / "out"
    result = json.loads(run_recipe("r", str(FIXTURES / "sales_week1.xlsx"), str(out)))
    assert result["step"] == "verify"
    assert "검증 불일치" in result["error"]
    assert not out.exists()


def test_real_mismatch_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """week1 엑셀로 만든 보고서를 week2 원본과 대조하면 불일치로 실패한다."""
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    monkeypatch.delenv(FORCE_VERIFY_FAIL_ENV, raising=False)
    made = json.loads(excel_to_report_ppt(str(FIXTURES / "sales_week1.xlsx"), "부서", "매출"))
    with pytest.raises(RecipeError) as info:
        verify_outputs(STEPS, {"$input": str(FIXTURES / "sales_week2.xlsx")}, made["path"])
    assert info.value.step == "verify"


def test_multi_report_verifies_only_producing_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """원본이 다른 보고서를 두 번 만들어도 최종 산출물은 마지막 원본으로만 검증한다."""
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    monkeypatch.delenv(FORCE_VERIFY_FAIL_ENV, raising=False)
    week1, week2 = str(FIXTURES / "sales_week1.xlsx"), str(FIXTURES / "sales_week2.xlsx")
    steps: list[dict[str, Any]] = [
        {
            "tool": "excel_to_report_ppt",
            "args": {"input": week1, "group_by": "부서", "value": "매출"},
        },
        {
            "tool": "excel_to_report_ppt",
            "args": {"input": "$input", "group_by": "부서", "value": "매출"},
        },
    ]
    made = json.loads(excel_to_report_ppt(week2, "부서", "매출"))
    variables = {"$input": week2, "$step1": "other.pptx", "$step2": made["path"]}
    verify_outputs(steps, variables, made["path"])


def test_template_on_earlier_report_traces_to_its_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """뒤에 다른 보고서가 있어도 양식 적용 산출물은 입력 체인의 원본으로 검증한다."""
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    monkeypatch.delenv(FORCE_VERIFY_FAIL_ENV, raising=False)
    week1, week2 = str(FIXTURES / "sales_week1.xlsx"), str(FIXTURES / "sales_week2.xlsx")
    steps: list[dict[str, Any]] = [
        {
            "tool": "excel_to_report_ppt",
            "args": {"input": week1, "group_by": "부서", "value": "매출"},
        },
        {
            "tool": "excel_to_report_ppt",
            "args": {"input": week2, "group_by": "부서", "value": "매출"},
        },
        {"tool": "apply_template", "args": {"input": "$step1", "template": "t"}},
    ]
    made = json.loads(excel_to_report_ppt(week1, "부서", "매출"))
    variables = {"$input": week2, "$step1": "x.pptx", "$step2": "y.pptx", "$step3": made["path"]}
    verify_outputs(steps, variables, made["path"])


def test_steps_in_one_run_keep_separate_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 실행 ID를 공유해도 단계마다 산출물 경로가 달라 서로 덮어쓰지 않는다."""
    from docmill import runlog, templates

    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    monkeypatch.delenv(FORCE_VERIFY_FAIL_ENV, raising=False)
    templates.register_template("t", FIXTURES / "template_default.pptx")
    steps: list[dict[str, Any]] = [
        {
            "tool": "excel_to_report_ppt",
            "args": {
                "input": str(FIXTURES / "sales_week1.xlsx"),
                "group_by": "부서",
                "value": "매출",
            },
        },
        {
            "tool": "excel_to_report_ppt",
            "args": {"input": "$input", "group_by": "부서", "value": "매출"},
        },
        {"tool": "apply_template", "args": {"input": "$step1", "template": "t"}},
    ]
    folder = tmp_path / "recipes"
    folder.mkdir(parents=True)
    (folder / "r.json").write_text(json.dumps({"steps": steps}), encoding="utf-8")
    token = runlog._current_run_id.set("shared-run")
    try:
        out = tmp_path / "out"
        result = json.loads(run_recipe("r", str(FIXTURES / "sales_week2.xlsx"), str(out)))
    finally:
        runlog._current_run_id.reset(token)
    assert "error" not in result, result
    outputs = sorted(p.name for p in (tmp_path / "outputs").glob("*.pptx"))
    assert outputs == [f"shared-run-step{n}.pptx" for n in (1, 2, 3)]
