"""워드 양식 등록·적용(FR-25) 단위 테스트."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from docmill import templates
from docmill.templates import TemplateError
from docmill.tools.template import apply_template, describe_rules

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "template_word.docx"
REPORT = FIXTURES / "report_headings.docx"


def test_extract_rules_per_level() -> None:
    rules = templates.extract_docx_rules(TEMPLATE)
    assert rules["kind"] == "docx"
    assert rules["headings"]["1"] == {
        "title_size": 20.0,
        "title_color": "1F4E79",
        "title_align": "center",
        "font": "Pretendard",
        "font_ea": "Pretendard",
    }
    assert rules["headings"]["3"]["title_align"] == "right"
    assert rules["title_size"] == 20.0  # level 1 mirrored at top level


def test_extract_is_deterministic() -> None:
    assert templates.extract_docx_rules(TEMPLATE) == templates.extract_docx_rules(TEMPLATE)


def test_register_docx_and_load(tmp_path: Path) -> None:
    rules = templates.register_template("w", TEMPLATE, home=tmp_path)
    assert templates.load_template("w", home=tmp_path) == json.loads(json.dumps(rules))
    assert templates.list_templates(home=tmp_path) == ["w"]


def test_docx_without_headings_is_rejected(tmp_path: Path) -> None:
    bare = tmp_path / "bare.docx"
    with zipfile.ZipFile(bare, "w") as z:
        z.writestr("word/styles.xml", '<w:styles xmlns:w="urn:x"/>')
        z.writestr("word/document.xml", '<w:document xmlns:w="urn:x"/>')
    with pytest.raises(TemplateError):
        templates.extract_docx_rules(bare)
    with pytest.raises(TemplateError):
        templates.extract_docx_rules(tmp_path / "missing.docx")


def test_describe_rules_lists_levels() -> None:
    lines = describe_rules(templates.extract_docx_rules(TEMPLATE))
    assert lines[0] == "제목 1 · 제목 크기: 20.0pt"
    assert any(line.startswith("제목 3 · ") for line in lines)


def test_apply_rejects_kind_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    templates.register_template("deck", FIXTURES / "template_default.pptx", home=tmp_path)
    out = json.loads(apply_template(str(REPORT), "deck"))
    assert out["step"] == "template" and "error" in out


def test_apply_rejects_unknown_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    out = json.loads(apply_template(str(tmp_path / "x.txt"), "w"))
    assert out["step"] == "input"
