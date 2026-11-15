"""양식 등록(FR-18)·apply_template(FR-07) 단위 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docmill import templates
from docmill.templates import TemplateError
from docmill.tools.template import apply_template, describe_rules

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_register_extracts_rules_and_lists_sorted(tmp_path: Path) -> None:
    rules = templates.register_template("b", FIXTURES / "template_default.pptx", home=tmp_path)
    templates.register_template("a", FIXTURES / "template_default.pptx", home=tmp_path)
    assert rules == {
        "title_size": 40.0,
        "title_color": "1F4E79",
        "title_align": "center",
        "font": "Pretendard",
    }
    assert templates.list_templates(home=tmp_path) == ["a", "b"]
    assert templates.load_template("a", home=tmp_path) == rules


def test_list_is_empty_without_templates(tmp_path: Path) -> None:
    assert templates.list_templates(home=tmp_path) == []


@pytest.mark.parametrize("name", ["", "  ", "../x", "a/b", ".."])
def test_bad_names_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(TemplateError):
        templates.register_template(name, FIXTURES / "template_default.pptx", home=tmp_path)


def test_missing_source_and_unknown_template(tmp_path: Path) -> None:
    with pytest.raises(TemplateError):
        templates.register_template("x", tmp_path / "none.pptx", home=tmp_path)
    with pytest.raises(TemplateError):
        templates.load_template("none", home=tmp_path)


def test_describe_rules_fixed_order() -> None:
    rules = {"font": "F", "title_align": "left", "title_color": "000000", "title_size": 20}
    assert describe_rules(rules) == [
        "제목 크기: 20pt",
        "제목 색: #000000",
        "제목 정렬: left",
        "제목 글꼴: F",
    ]


def test_apply_reports_input_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    missing = json.loads(apply_template(str(tmp_path / "none.pptx"), "x"))
    assert missing["step"] == "input"
    source = tmp_path / "a.pptx"
    source.write_bytes(b"x")
    unknown = json.loads(apply_template(str(source), "nope"))
    assert unknown["step"] == "template"
    assert unknown["run_id"]
