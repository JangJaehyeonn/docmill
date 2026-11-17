"""guarded_edit(FR-31) 단위 테스트."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from docmill.tools.edits import Parts, apply_edit, cell_xml, paragraph_xml, set_shape_text
from docmill.tools.guard import GuardError, guard_edit, guarded_edit, summary
from docmill.tools.ooxml import OoxmlError, Package, element_spans, fingerprint_xml, rels

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PPTX = FIXTURES / "company_intro.pptx"
XLSX = FIXTURES / "sales_q3.xlsx"
DOCX = FIXTURES / "meeting_1111.docx"


def _parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def _leaky(extra: dict[str, bytes], drop: str | None = None) -> Any:
    """기본 편집을 한 뒤 범위 밖 파트를 덮어쓰거나 지우는 테스트용 편집 함수."""

    def editor(kind: str, parts: Parts, scope: dict[str, Any], edit: dict[str, Any]) -> Parts:
        edited = apply_edit(kind, parts, scope, edit)
        edited.update(extra)
        if drop:
            edited.pop(drop)
        return edited

    return editor


# ── ooxml ────────────────────────────────────────────────────────────────


def test_element_spans_finds_outer_elements_and_skips_comments() -> None:
    text = '<r><!-- <a> --><a x=">"><a/></a><b/><a/></r>'
    spans = element_spans(text, lambda n, p: n == "a" and p == ("r",))
    assert [text[s.start : s.end] for s in spans] == ['<a x=">"><a/></a>', "<a/>"]


def test_element_spans_rejects_broken_xml() -> None:
    with pytest.raises(OoxmlError):
        element_spans("<r><a></r>", lambda n, p: False)


def test_fingerprint_ignores_attribute_order_but_not_text() -> None:
    assert fingerprint_xml('<a x="1" y="2"/>') == fingerprint_xml("<a y='2' x='1'></a>")
    assert fingerprint_xml("<a>1</a>") != fingerprint_xml("<a>2</a>")


def test_rels_resolves_relative_and_absolute_targets() -> None:
    xml = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="a" Target="/xl/s.xml"/><Relationship Id="b" Target="../t.xml"/>'
        "</Relationships>"
    )
    assert rels({"xl/_rels/workbook.xml.rels": xml.encode()}, "xl/workbook.xml") == {
        "a": "xl/s.xml",
        "b": "t.xml",
    }


def test_package_write_keeps_parts_and_is_deterministic() -> None:
    package = Package.read(PPTX.read_bytes())
    first = package.write(package.parts)
    assert _parts(first) == _parts(PPTX.read_bytes())
    assert first == package.write(dict(package.parts))


# ── edits ────────────────────────────────────────────────────────────────


def test_set_shape_text_keeps_first_run_format() -> None:
    shape = (
        "<p:sp><p:txBody><a:bodyPr/><a:p><a:pPr algn='l'/><a:r><a:rPr sz='3200'/><a:t>x</a:t>"
        "</a:r></a:p><a:p><a:r><a:t>y</a:t></a:r></a:p></p:txBody></p:sp>"
    )
    out = set_shape_text(shape, "가\n<b>")
    assert out.count("<a:p>") == 2 and "<a:t>y</a:t>" not in out
    assert "<a:rPr sz='3200'/><a:t>가</a:t>" in out and "&lt;b&gt;" in out


def test_cell_xml_writes_each_value_type() -> None:
    cell = '<c r="C2" s="1" t="n"><v>1</v></c>'
    assert cell_xml(cell, 999) == '<c r="C2" s="1" t="n"><v>999</v></c>'
    assert cell_xml(cell, True) == '<c r="C2" s="1" t="b"><v>1</v></c>'
    assert 't="inlineStr"><is><t xml:space="preserve">a&amp;b</t></is>' in cell_xml(cell, "a&b")
    assert cell_xml(cell, None) == '<c r="C2" s="1"/>'


def test_paragraph_xml_keeps_paragraph_style() -> None:
    p = '<w:p><w:pPr><w:pStyle w:val="H"/></w:pPr><w:r><w:t>old</w:t></w:r></w:p>'
    assert paragraph_xml(p, "새") == (
        '<w:p><w:pPr><w:pStyle w:val="H"/></w:pPr><w:r><w:t xml:space="preserve">새</w:t>'
        "</w:r></w:p>"
    )


# ── guard ────────────────────────────────────────────────────────────────

PPT_SCOPE = {"slides": [2], "shapes": [2]}


def test_pptx_in_scope_edit_changes_only_that_shape() -> None:
    data, outcome = guard_edit(
        ".pptx",
        PPTX.read_bytes(),
        PPT_SCOPE,
        {"set_text": [{"slide": 2, "shape": 2, "text": "새"}]},
    )
    before, after = _parts(PPTX.read_bytes()), _parts(data)
    assert outcome.reverted == [] and outcome.changed == ["슬라이드 2 도형 2"]
    assert [n for n in before if before[n] != after[n]] == ["ppt/slides/slide2.xml"]
    assert "범위 밖 변경 0곳" in summary(outcome)


def test_pptx_out_of_scope_shapes_parts_and_structure_are_reverted() -> None:
    editor = _leaky({"ppt/new.xml": b"<x/>"}, drop="docProps/app.xml")
    edit = {
        "set_text": [
            {"slide": 2, "shape": 2, "text": "새"},
            {"slide": 2, "shape": 3, "text": "몰래"},
        ]
    }
    data, outcome = guard_edit(".pptx", PPTX.read_bytes(), PPT_SCOPE, edit, editor)
    locations = {r["location"]: r["change"] for r in outcome.reverted}
    assert locations == {
        "슬라이드 2 도형 3": "바뀜",
        "파일 docProps/app.xml": "삭제됨",
        "파일 ppt/new.xml": "추가됨",
    }
    after = _parts(data)
    assert set(after) == set(_parts(PPTX.read_bytes()))
    assert b"\xeb\xaa\xb0\xeb\x9e\x98" not in after["ppt/slides/slide2.xml"]  # "몰래"


def test_pptx_equivalent_rewrite_is_not_a_change_and_keeps_original_bytes() -> None:
    original = _parts(PPTX.read_bytes())["ppt/slides/slide3.xml"]
    rewritten = original.replace(b'name="Title 1"', b'name="Title 1" ')
    editor = _leaky({"ppt/slides/slide3.xml": rewritten})
    edit = {"set_text": [{"slide": 2, "shape": 2, "text": "새"}]}
    data, outcome = guard_edit(".pptx", PPTX.read_bytes(), PPT_SCOPE, edit, editor)
    assert outcome.reverted == []
    assert _parts(data)["ppt/slides/slide3.xml"] == original


def test_pptx_skeleton_change_outside_shapes_is_reverted() -> None:
    original = _parts(PPTX.read_bytes())["ppt/slides/slide2.xml"]
    broken = original.replace(b"<a:masterClrMapping/>", b"")
    editor = _leaky({"ppt/slides/slide2.xml": broken})
    edit = {"set_text": [{"slide": 2, "shape": 2, "text": "새"}]}
    data, outcome = guard_edit(".pptx", PPTX.read_bytes(), PPT_SCOPE, edit, editor)
    assert outcome.reverted == [{"location": "슬라이드 2의 도형 밖 구조", "change": "바뀜"}]
    assert _parts(data)["ppt/slides/slide2.xml"] == original


def test_pptx_whole_slide_scope_allows_any_change_on_that_slide() -> None:
    edit = {"set_text": [{"slide": 2, "shape": 3, "text": "본문"}]}
    data, outcome = guard_edit(".pptx", PPTX.read_bytes(), {"slides": [2]}, edit)
    assert outcome.reverted == [] and outcome.changed == ["슬라이드 2"]
    assert "본문".encode() in _parts(data)["ppt/slides/slide2.xml"]


@pytest.mark.parametrize("scope", [{"slides": [2]}, PPT_SCOPE])
def test_control_characters_do_not_produce_a_corrupt_part(scope: dict[str, Any]) -> None:
    original = _parts(PPTX.read_bytes())["ppt/slides/slide2.xml"]
    edit = {"set_text": [{"slide": 2, "shape": 2, "text": "깨짐\x01"}]}
    data, outcome = guard_edit(".pptx", PPTX.read_bytes(), scope, edit)
    assert [r["change"] for r in outcome.reverted] == ["XML이 깨짐"]
    assert _parts(data)["ppt/slides/slide2.xml"] == original


def test_set_text_rejects_shape_with_several_text_bodies() -> None:
    shape = "<p:sp><p:txBody><a:p/></p:txBody><p:txBody><a:p/></p:txBody></p:sp>"
    with pytest.raises(Exception, match="글자 상자"):
        set_shape_text(shape, "x")


def test_xlsx_cells_outside_range_are_reverted() -> None:
    scope = {"sheet": 1, "range": "C2:C4"}
    data, outcome = guard_edit(
        ".xlsx", XLSX.read_bytes(), scope, {"set_cells": {"C2": 999, "c10": 111}}
    )
    assert outcome.changed == ["시트 1 셀 C2"]
    assert outcome.reverted == [{"location": "시트 1 셀 C10", "change": "바뀜"}]
    sheet = _parts(data)["xl/worksheets/sheet1.xml"]
    assert b"<v>999</v>" in sheet and b"<v>111</v>" not in sheet


def test_docx_paragraphs_outside_scope_are_reverted() -> None:
    edit = {"set_paragraphs": {"3": "새 문장", "5": "몰래"}}
    data, outcome = guard_edit(".docx", DOCX.read_bytes(), {"paragraphs": [3]}, edit)
    assert outcome.changed == ["문단 3"]
    assert outcome.reverted == [{"location": "문단 5", "change": "바뀜"}]
    assert "몰래".encode() not in _parts(data)["word/document.xml"]


@pytest.mark.parametrize(
    ("kind", "src", "scope", "edit", "step"),
    [
        (".pptx", PPTX, {"slides": [9]}, {"set_text": []}, "scope"),
        (".pptx", PPTX, {"slides": [2], "pages": 1}, {"set_text": []}, "scope"),
        (".pptx", PPTX, PPT_SCOPE, {"set_text": [{"slide": 2, "shape": 99, "text": "x"}]}, "edit"),
        (".xlsx", XLSX, {"sheet": 1, "range": "C2:C4"}, {"set_cells": {"Z99": 1}}, "edit"),
        (".xlsx", XLSX, {"sheet": 1, "range": "2:4"}, {"set_cells": {"C2": 1}}, "scope"),
        (".docx", DOCX, {"paragraphs": [3]}, {"set_paragraphs": {"99": "x"}}, "edit"),
        (".docx", DOCX, {"paragraphs": [3]}, {"set_cells": {"A1": 1}}, "edit"),
    ],
)
def test_invalid_scope_or_edit_reports_step(
    kind: str, src: Path, scope: dict[str, Any], edit: dict[str, Any], step: str
) -> None:
    with pytest.raises(GuardError) as info:
        guard_edit(kind, src.read_bytes(), scope, edit)
    assert info.value.step == step


# ── tool ─────────────────────────────────────────────────────────────────


def test_tool_writes_output_and_is_byte_identical(tmp_path: Path) -> None:
    edit = {
        "set_text": [{"slide": 2, "shape": 2, "text": "새"}, {"slide": 3, "shape": 2, "text": "x"}]
    }
    a = json.loads(guarded_edit(str(PPTX), PPT_SCOPE, edit, str(tmp_path / "a")))
    b = json.loads(guarded_edit(str(PPTX), PPT_SCOPE, edit, str(tmp_path / "b")))
    assert a["out_of_scope_changes"] == 1
    assert a["reverted"] == [{"location": "슬라이드 3 도형 2", "change": "바뀜"}]
    assert "범위 밖 변경 1곳 되돌림" in a["summary"]
    assert Path(a["path"]).read_bytes() == Path(b["path"]).read_bytes()
    assert not list((tmp_path / "a").glob(".*.part"))


def test_tool_reports_errors_instead_of_raising(tmp_path: Path) -> None:
    bad = tmp_path / "x.pptx"
    bad.write_bytes(b"not a zip")
    assert json.loads(guarded_edit(str(bad), PPT_SCOPE, {}))["step"] == "input"
    assert json.loads(guarded_edit(str(tmp_path / "x.hwp"), {}, {}))["step"] == "input"
    out = json.loads(guarded_edit(str(PPTX), {"slides": "2"}, {"set_text": []}, str(tmp_path)))
    assert out["step"] == "scope" and "error" in out
