"""문장 출처(FR-47) 단위 테스트: 보고서 문장 근거, 할 일 문단 위치, review_map 문장 검사."""

import json
import zipfile
from pathlib import Path

import pytest

from docmill.tools.provenance import (
    ProvenanceError,
    SentenceSpec,
    SheetTable,
    Sources,
    collect_sentences,
    dump,
    load_sentences,
    provenance_path,
    retext_sentences,
    sentence_key,
)
from docmill.tools.report import Group, sentence_specs
from docmill.tools.review import UNFOUNDED, UNSOURCED, build_review
from docmill.tools.tasks import Task, locate_tasks, source_paragraphs, task_sentences

_PRES = (
    '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>'
)
_PRES_RELS = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/></Relationships>'
)


def _shape(shape_id: int, lines: list[str], ph: str | None = None) -> str:
    nv = f'<p:nvPr><p:ph type="{ph}"/></p:nvPr>' if ph else "<p:nvPr/>"
    paras = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in lines)
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="s"/><p:cNvSpPr/>{nv}</p:nvSpPr>'
        f"<p:txBody>{paras}</p:txBody></p:sp>"
    )


def _pptx(path: Path, shapes: str) -> Path:
    slide = (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f"<p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml", _PRES)
        z.writestr("ppt/_rels/presentation.xml.rels", _PRES_RELS)
        z.writestr("ppt/slides/slide1.xml", slide)
    return path


def _docx(path: Path, paragraphs: list[str]) -> Path:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        document = f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>'
        z.writestr("word/document.xml", document)
    return path


def _sources() -> Sources:
    values = [["부서", "분기", "매출"], ["A팀", "Q3", 10], ["B팀", "Q3", 30], ["A팀", "Q3", 5]]
    return Sources(SheetTable("/w/sales.xlsx", "매출", 1, 1, values), "부서", "매출")


# ── 보고서 ──────────────────────────────────────────────────────────────


def test_sentence_specs_cover_summary_and_detail_lines_with_ranges() -> None:
    groups = [Group("A팀", 15.0, 33.33), Group("B팀", 30.0, 66.67)]
    args = {"slides": 5, "value": "매출", "group_by": "부서"}
    specs = sentence_specs(groups, args, _sources())
    summary, detail = specs[1], specs[3]
    assert [s.text for s in summary] == ["매출 합계: 45", "부서 수: 2개", "최대: B팀 30 (66.7%)"]
    assert [s.source["range"] for s in summary] == ["C2:C4", "A2:A4", "C2:C4"]
    assert summary[2].source["criteria"] == "B팀"
    assert [s.text for s in detail] == ["A팀: 15 (33.3%)", "B팀: 30 (66.7%)"]
    assert all(s.source["file"] == "/w/sales.xlsx" for s in summary + detail)
    assert set(specs) == {1, 3}


def test_collect_sentences_finds_shape_of_each_line(tmp_path: Path) -> None:
    pptx = _pptx(
        tmp_path / "r.pptx",
        _shape(2, ["요약"], ph="title") + _shape(3, ["매출 합계: 45", "부서 수: 2개"]),
    )
    specs = {0: [SentenceSpec("부서 수: 2개", {"range": "A2:A4"})]}
    found = collect_sentences(pptx, ["summary"], specs)
    location = {"slide": 1, "shape": "3"}
    assert found == [{"text": "부서 수: 2개", "location": location, "source": {"range": "A2:A4"}}]
    with pytest.raises(ProvenanceError):
        collect_sentences(pptx, ["summary"], {0: [SentenceSpec("없는 문장", {})]})
    with pytest.raises(ProvenanceError):
        collect_sentences(pptx, ["cover", "summary"], specs)


def test_dump_and_load_sentences_round_trip(tmp_path: Path) -> None:
    doc = tmp_path / "r.pptx"
    provenance_path(doc).write_bytes(dump([], [{"text": "a"}]))
    assert load_sentences(doc) == [{"text": "a"}]
    assert json.loads(dump([]))["numbers"] == [] and "sentences" not in json.loads(dump([]))
    provenance_path(doc).write_bytes(dump([]))
    assert load_sentences(doc) is None
    assert load_sentences(tmp_path / "none.pptx") is None


def test_retext_sentences_follows_refreshed_numbers(tmp_path: Path) -> None:
    pptx = _pptx(tmp_path / "r.pptx", _shape(3, ["매출 합계: 1,500", "부서 수: 2개"]))
    sentences = [
        {"text": "매출 합계: 1,200", "location": {"slide": 1, "shape": "3"}, "source": {}},
        {"text": "부서 수: 2개", "location": {"slide": 1, "shape": "3"}, "source": {}},
        {"text": "다른 말", "location": {"slide": 1, "shape": "3"}, "source": {}},
    ]
    renewed = retext_sentences(pptx.read_bytes(), sentences)
    assert [s["text"] for s in renewed] == ["매출 합계: 1,500", "부서 수: 2개", "다른 말"]
    assert sentence_key("최대: A팀 4,050 (39.9%)") == "최대: A팀 # (#%)"


def test_retext_sentences_keeps_digits_in_item_names(tmp_path: Path) -> None:
    pptx = _pptx(tmp_path / "r.pptx", _shape(3, ["1팀: 20 (33.3%)", "2팀: 40 (66.7%)"]))
    loc = {"slide": 1, "shape": "3"}
    sentences = [
        {"text": "1팀: 10 (25.0%)", "location": loc, "source": {"criteria": "1팀"}},
        {"text": "2팀: 30 (75.0%)", "location": loc, "source": {"criteria": "2팀"}},
    ]
    renewed = retext_sentences(pptx.read_bytes(), sentences)
    assert [s["text"] for s in renewed] == ["1팀: 20 (33.3%)", "2팀: 40 (66.7%)"]
    assert sentence_key("1팀: 10") != sentence_key("2팀: 10")


def test_retext_sentences_skips_used_lines_with_same_key(tmp_path: Path) -> None:
    pptx = _pptx(tmp_path / "r.pptx", _shape(3, ["합계: 5", "합계: 7"]))
    loc = {"slide": 1, "shape": "3"}
    sentences = [{"text": "합계: 1", "location": loc}, {"text": "합계: 2", "location": loc}]
    assert [s["text"] for s in retext_sentences(pptx.read_bytes(), sentences)] == [
        "합계: 5",
        "합계: 7",
    ]


@pytest.mark.parametrize(
    ("old", "new"), [("매출 합계: 100", "매출 합계: -100"), ("매출 합계: -100", "매출 합계: 100")]
)
def test_retext_sentences_follows_sign_change(tmp_path: Path, old: str, new: str) -> None:
    pptx = _pptx(tmp_path / "r.pptx", _shape(3, [new]))
    sentences = [{"text": old, "location": {"slide": 1, "shape": "3"}, "source": {}}]
    assert [s["text"] for s in retext_sentences(pptx.read_bytes(), sentences)] == [new]
    assert sentence_key(old) == sentence_key(new)


# ── 할 일 표 ────────────────────────────────────────────────────────────


def test_locate_tasks_maps_rows_to_paragraphs_in_document_order(tmp_path: Path) -> None:
    lines = [
        "회의록",
        "할 일: 보고서 / 담당: 김 / 마감: 2026-01-02",
        "기타",
        "할 일: 보고서 / 담당: 김 / 마감: 2026-01-02",
        "할 일: 발표 / 담당: 이 / 마감: 2026-01-01",
    ]
    paragraphs = source_paragraphs(_docx(tmp_path / "m.docx", lines))
    assert paragraphs == lines
    same = Task("보고서", "김", "2026-01-02")
    kept = [Task("발표", "이", "2026-01-01"), same, same, Task("없음", "박", "2026-01-03")]
    assert locate_tasks(kept, paragraphs) == [4, 1, 3, None]
    rows = task_sentences(kept, Path("/w/m.docx"), paragraphs)
    assert [r["location"]["row"] for r in rows] == [2, 3, 4, 5]
    assert rows[0] == {
        "text": "발표",
        "location": {"row": 2, "range": "A2:D2"},
        "source": {"file": "/w/m.docx", "paragraph": 4},
    }
    assert "paragraph" not in rows[3]["source"]


# ── review_map ─────────────────────────────────────────────────────────


def _reviewed(tmp_path: Path, shapes: str, sentences: list[dict[str, object]] | None) -> list:
    doc = _pptx(tmp_path / "d.pptx", shapes)
    numbers = [{"value": 45, "kind": "summary", "location": {"slide": 1, "shape": "3"}}]
    provenance_path(doc).write_bytes(dump(numbers, sentences))
    return list(build_review(doc)["items"])


def _recorded(text: str, shape: str = "3") -> dict[str, object]:
    return {"text": text, "location": {"slide": 1, "shape": shape}, "source": {}}


def test_review_flags_body_sentence_without_source(tmp_path: Path) -> None:
    shapes = (
        _shape(2, ["요약"], ph="title")
        + _shape(3, ["매출 합계: 45"])
        + _shape(4, ["이번 분기 매출은 크게 개선되었다."])
        + _shape(5, ["감사합니다"], ph="subTitle")
    )
    items = _reviewed(tmp_path, shapes, [_recorded("매출 합계: 45")])
    assert items == [
        {
            "reason": UNSOURCED,
            "text": "이번 분기 매출은 크게 개선되었다.",
            "location": {"slide": 1, "shape": "4", "paragraph": 1},
        }
    ]


def test_review_ignores_sentences_without_sentence_provenance(tmp_path: Path) -> None:
    shapes = _shape(3, ["매출 합계: 45"]) + _shape(4, ["이번 분기 매출은 크게 개선되었다."])
    assert _reviewed(tmp_path, shapes, None) == []


def test_review_reports_sentence_once_when_number_already_flagged(tmp_path: Path) -> None:
    shapes = _shape(3, ["매출 합계: 45"]) + _shape(4, ["기타 비용 4,321", "매출이 최고일 것이다."])
    items = _reviewed(tmp_path, shapes, [_recorded("매출 합계: 45")])
    assert [(i["reason"], i["text"]) for i in items] == [
        (UNSOURCED, "4,321"),
        (UNFOUNDED, "매출이 최고일 것이다."),
    ]


def test_review_accepts_sentence_whose_number_was_refreshed(tmp_path: Path) -> None:
    # 새로 고침으로 숫자만 바뀐 문장은 같은 문장으로 본다 (숫자 45는 numbers에 출처가 있다).
    assert _reviewed(tmp_path, _shape(3, ["매출 합계: 45"]), [_recorded("매출 합계: 40")]) == []
    # 다른 도형에 적힌 같은 문장은 출처로 치지 않는다.
    items = _reviewed(tmp_path, _shape(3, ["매출 합계: 45"]), [_recorded("매출 합계: 45", "9")])
    assert [i["text"] for i in items] == ["매출 합계: 45"]
