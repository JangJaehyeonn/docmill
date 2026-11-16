"""워드 양식 등록·적용(FR-25) 단위 테스트."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

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


_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
)
_THEME = (
    f"<a:theme {_NS}><a:themeElements><a:clrScheme>"
    '<a:accent3><a:srgbClr val="A5A5A5"/></a:accent3></a:clrScheme>'
    '<a:fontScheme><a:majorFont><a:latin typeface="Major Sans"/><a:ea typeface="Major Ko"/>'
    '<a:font script="Hang" typeface="Major Hang"/>'
    '</a:majorFont><a:minorFont><a:latin typeface="Minor Sans"/><a:ea typeface=""/>'
    '<a:font script="Jpan" typeface="Minor Jpan"/><a:font script="Hang" typeface="Minor Hang"/>'
    "</a:minorFont></a:fontScheme></a:themeElements></a:theme>"
)


def _make_docx(
    path: Path, styles: str, body: str, theme: bool = True, ea_lang: str | None = None
) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/styles.xml", f"<w:styles {_NS}>{styles}</w:styles>")
        z.writestr("word/document.xml", f"<w:document {_NS}><w:body>{body}</w:body></w:document>")
        if theme:
            z.writestr("word/theme/theme1.xml", _THEME)
        if ea_lang:
            z.writestr(
                "word/settings.xml",
                f'<w:settings {_NS}><w:themeFontLang w:val="en-US" w:eastAsia="{ea_lang}"/>'
                "</w:settings>",
            )
    return path


def test_doc_defaults_fill_missing_size(tmp_path: Path) -> None:
    styles = (
        '<w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="24"/></w:rPr></w:rPrDefault>'
        '<w:pPrDefault><w:pPr><w:jc w:val="both"/></w:pPr></w:pPrDefault></w:docDefaults>'
        '<w:style w:styleId="Heading1"><w:name w:val="heading 1"/>'
        '<w:rPr><w:color w:val="112233"/></w:rPr></w:style>'
    )
    rules = templates.extract_docx_rules(_make_docx(tmp_path / "d.docx", styles, ""))
    assert rules["headings"]["1"] == {
        "title_size": 12.0,
        "title_align": "justify",
        "title_color": "112233",
    }


def test_theme_color_and_fonts_are_resolved(tmp_path: Path) -> None:
    styles = (
        '<w:style w:styleId="Heading1"><w:name w:val="heading 1"/><w:rPr>'
        '<w:rFonts w:asciiTheme="majorHAnsi" w:eastAsiaTheme="majorEastAsia"/>'
        '<w:color w:val="FFFFFF" w:themeColor="accent3"/></w:rPr></w:style>'
        '<w:style w:styleId="Heading2"><w:name w:val="heading 2"/><w:rPr>'
        '<w:rFonts w:asciiTheme="minorHAnsi" w:eastAsiaTheme="minorEastAsia" w:eastAsia="Gulim"/>'
        '<w:color w:val="404040" w:themeColor="accent3" w:themeShade="BF"/></w:rPr></w:style>'
    )
    rules = templates.extract_docx_rules(_make_docx(tmp_path / "t.docx", styles, ""))
    h1, h2 = rules["headings"]["1"], rules["headings"]["2"]
    assert h1 == {"font": "Major Sans", "font_ea": "Major Ko", "title_color": "A5A5A5"}
    assert h2["title_color"] == "404040"  # shade present: stored literal wins
    assert h2["font"] == "Minor Sans" and h2["font_ea"] == "Gulim"  # empty theme ea falls back


_EA_THEME_STYLES = (
    '<w:style w:styleId="Heading1"><w:name w:val="heading 1"/><w:rPr>'
    '<w:rFonts w:eastAsiaTheme="majorEastAsia"/></w:rPr></w:style>'
    '<w:style w:styleId="Heading2"><w:name w:val="heading 2"/><w:rPr>'
    '<w:rFonts w:eastAsiaTheme="minorEastAsia"/></w:rPr></w:style>'
)


@pytest.mark.parametrize(
    ("lang", "expected"), [("ko-KR", "Minor Hang"), ("ja-JP", "Minor Jpan"), ("zh-CN", None)]
)
def test_empty_theme_ea_uses_theme_font_lang_script(
    tmp_path: Path, lang: str, expected: str | None
) -> None:
    path = _make_docx(tmp_path / "l.docx", _EA_THEME_STYLES, "", ea_lang=lang)
    headings = templates.extract_docx_rules(path)["headings"]
    assert headings["1"]["font_ea"] == "Major Ko"  # non-empty a:ea wins over script fonts
    assert headings.get("2", {}).get("font_ea") == expected


def test_empty_theme_ea_without_settings_stays_unresolved(tmp_path: Path) -> None:
    rules = templates.extract_docx_rules(_make_docx(tmp_path / "n.docx", _EA_THEME_STYLES, ""))
    assert "2" not in rules["headings"]


def test_theme_color_aliases_map_to_drawingml_slots() -> None:
    ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    theme = ElementTree.fromstring(
        f'<a:theme xmlns:a="{ns}"><a:themeElements><a:clrScheme name="t">'
        '<a:dk1><a:srgbClr val="111111"/></a:dk1><a:lt1><a:srgbClr val="EEEEEE"/></a:lt1>'
        '<a:hlink><a:srgbClr val="0000FF"/></a:hlink>'
        '<a:folHlink><a:srgbClr val="800080"/></a:folHlink>'
        "</a:clrScheme></a:themeElements></a:theme>"
    )
    assert templates._theme_color_hex(theme, "dark1") == "111111"
    assert templates._theme_color_hex(theme, "light1") == "EEEEEE"
    assert templates._theme_color_hex(theme, "hyperlink") == "0000FF"
    assert templates._theme_color_hex(theme, "followedHyperlink") == "800080"


def test_style_name_maps_to_level(tmp_path: Path) -> None:
    styles = (
        '<w:style w:styleId="CorpH1"><w:name w:val="heading 1"/>'
        '<w:rPr><w:sz w:val="40"/></w:rPr></w:style>'
    )
    body = '<w:p><w:pPr><w:pStyle w:val="CorpH1"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p>'
    path = _make_docx(tmp_path / "n.docx", styles, body)
    assert templates.extract_docx_rules(path)["headings"]["1"]["title_size"] == 20.0
    assert _positions(path) == [(1, "1")]


def _positions(path: Path) -> list[tuple[int, str]]:
    from docmill.tools.template import _word_heading_positions

    return _word_heading_positions(path)


def test_direct_formatting_on_named_style_paragraph(tmp_path: Path) -> None:
    styles = (
        '<w:style w:styleId="CorpH1"><w:name w:val="heading 1"/>'
        '<w:rPr><w:sz w:val="40"/></w:rPr></w:style>'
    )
    body = (
        '<w:p><w:pPr><w:pStyle w:val="CorpH1"/></w:pPr>'
        '<w:r><w:rPr><w:sz w:val="48"/></w:rPr><w:t>x</w:t></w:r></w:p>'
    )
    rules = templates.extract_docx_rules(_make_docx(tmp_path / "p.docx", styles, body))
    assert rules["headings"]["1"]["title_size"] == 24.0


def test_format_heading_passes_east_asian_font() -> None:
    from typing import Any

    from docmill.tools import template as tool

    calls: list[tuple[str, dict[str, Any]]] = []

    def fake(engine: Any, command: str, params: dict[str, Any]) -> None:
        calls.append((command, params))

    original = tool._command
    tool._command = fake  # type: ignore[assignment]
    try:
        tool._format_heading(None, 3, {"font": "Arial", "font_ea": "Gulim"})  # type: ignore[arg-type]
    finally:
        tool._command = original
    assert ("format.font", {"name": "Arial", "eastAsia": "Gulim"}) in calls
