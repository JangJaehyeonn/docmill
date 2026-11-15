"""양식 등록(FR-18)·apply_template(FR-07) 단위 테스트."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from docmill import templates
from docmill.templates import TemplateError
from docmill.tools.template import _collect_titles, apply_template, describe_rules

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


def _inherited_pptx(path: Path) -> Path:
    """제목 직접 서식 없이 마스터 titleStyle(테마 색·글꼴)만 가진 최소 pptx를 만든다."""
    ns = (
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    )
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    def rels(kind: str, target: str) -> str:
        return (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="r1" Type="{rel}/{kind}" Target="{target}"/></Relationships>'
        )

    slide = (
        f"<p:sld {ns}><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id='2' name='t'/><p:cNvSpPr/>"
        "<p:nvPr><p:ph type='title'/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>x</a:t></a:r>"
        "</a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    master = (
        f"<p:sldMaster {ns}><p:cSld><p:spTree/></p:cSld>"
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2"/><p:txStyles><p:titleStyle>'
        '<a:lvl1pPr algn="r"><a:defRPr sz="3200"><a:solidFill><a:schemeClr val="accent1"/>'
        '</a:solidFill><a:latin typeface="+mj-lt"/></a:defRPr></a:lvl1pPr>'
        "</p:titleStyle></p:txStyles></p:sldMaster>"
    )
    layout = f"<p:sldLayout {ns}><p:cSld><p:spTree/></p:cSld></p:sldLayout>"
    theme = (
        f"<a:theme {ns}><a:themeElements><a:clrScheme name='c'>"
        "<a:accent1><a:srgbClr val='aabbcc'/></a:accent1></a:clrScheme>"
        "<a:fontScheme name='f'><a:majorFont><a:latin typeface='Major'/></a:majorFont>"
        "</a:fontScheme></a:themeElements></a:theme>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/slides/slide1.xml", slide)
        z.writestr(
            "ppt/slides/_rels/slide1.xml.rels", rels("slideLayout", "../slideLayouts/l1.xml")
        )
        z.writestr("ppt/slideLayouts/l1.xml", layout)
        z.writestr(
            "ppt/slideLayouts/_rels/l1.xml.rels", rels("slideMaster", "../slideMasters/m1.xml")
        )
        z.writestr("ppt/slideMasters/m1.xml", master)
        z.writestr("ppt/slideMasters/_rels/m1.xml.rels", rels("theme", "../theme/t1.xml"))
        z.writestr("ppt/theme/t1.xml", theme)
    return path


def test_extract_inherits_master_style_and_theme_color(tmp_path: Path) -> None:
    rules = templates.extract_rules(_inherited_pptx(tmp_path / "inh.pptx"))
    assert rules == {
        "title_align": "right",
        "title_size": 32.0,
        "title_color": "AABBCC",
        "font": "Major",
    }


def test_collect_titles_descends_into_groups() -> None:
    shapes = [
        {"id": 1, "placeholder": "body"},
        {"id": 2, "children": [{"id": 3, "placeholder": "ctrTitle"}, {"id": 4}]},
        {"id": 5, "placeholder": "title"},
    ]
    assert _collect_titles(shapes) == [3, 5]


def _props(xml: str) -> dict[str, object]:
    ns = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    rpr = ElementTree.fromstring(f"<a:rPr {ns} sz='2000'>{xml}</a:rPr>")
    return templates._read_props(None, rpr, None, None)


def test_srgb_luminance_transforms_applied() -> None:
    white = (
        "<a:solidFill><a:srgbClr val='000000'><a:lumOff val='100000'/></a:srgbClr></a:solidFill>"
    )
    assert _props(white)["title_color"] == "FFFFFF"
    plain = "<a:solidFill><a:srgbClr val='ff0000'/></a:solidFill>"
    assert _props(plain)["title_color"] == "FF0000"


def test_east_asian_font_extracted_separately() -> None:
    rules = _props("<a:latin typeface='Arial'/><a:ea typeface='맑은 고딕'/>")
    assert rules["font"] == "Arial"
    assert rules["font_ea"] == "맑은 고딕"
