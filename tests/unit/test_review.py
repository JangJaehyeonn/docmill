import json
import zipfile
from pathlib import Path

from docmill.tools.review import (
    CHANGED,
    UNFOUNDED,
    UNSOURCED,
    Provenance,
    Segment,
    check_segments,
    diff_segments,
    read_segments,
    review_map,
)

_PRES = (
    '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>'
)
_PRES_RELS = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/></Relationships>'
)
_SLIDE_RELS = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId2" Type="chart" Target="../charts/chart1.xml"/></Relationships>'
)
_CHART = (
    '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart>'
    '<c:ser><c:cat><c:strRef><c:strCache><c:pt idx="0"><c:v>개발팀</c:v></c:pt></c:strCache>'
    '</c:strRef></c:cat><c:val><c:numRef><c:numCache><c:pt idx="0"><c:v>{value}</c:v></c:pt>'
    "</c:numCache></c:numRef></c:val></c:ser></c:chart></c:chartSpace>"
)


def _slide(texts: list[str]) -> str:
    shapes = "".join(
        f'<p:sp><p:nvSpPr><p:cNvPr id="{n}" name="s"/></p:nvSpPr><p:txBody>'
        f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p></p:txBody></p:sp>"
        for n, t in enumerate(texts, start=10)
    )
    frame = (
        '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="99" name="c"/></p:nvGraphicFramePr>'
        '<a:graphic><a:graphicData><c:chart r:id="rId2"/></a:graphicData></a:graphic>'
        "</p:graphicFrame>"
    )
    return (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<p:cSld><p:spTree>{shapes}{frame}</p:spTree></p:cSld></p:sld>"
    )


def _pptx(path: Path, texts: list[str], value: str = "930") -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml", _PRES)
        z.writestr("ppt/_rels/presentation.xml.rels", _PRES_RELS)
        z.writestr("ppt/slides/slide1.xml", _slide(texts))
        z.writestr("ppt/slides/_rels/slide1.xml.rels", _SLIDE_RELS)
        z.writestr("ppt/charts/chart1.xml", _CHART.format(value=value))
    return path


def _provenance(path: Path) -> None:
    numbers = [
        {"value": 10150, "kind": "summary", "location": {"slide": 1, "shape": "10"}},
        {
            "value": 930,
            "kind": "chart",
            "location": {"slide": 1, "shape": "99", "chart": "chart1", "series": 1, "point": 0},
        },
    ]
    side = path.with_name(path.name + ".provenance.json")
    side.write_text(json.dumps({"numbers": numbers}), encoding="utf-8")


def _items(doc: Path, baseline: Path | None = None) -> list[dict[str, object]]:
    result = json.loads(review_map(str(doc), str(baseline) if baseline else None))
    return list(result["items"])


def test_sourced_numbers_are_not_flagged_and_unsourced_are(tmp_path: Path) -> None:
    doc = _pptx(tmp_path / "a.pptx", ["매출 합계: 10,150", "영업1팀 기타 비용 9,999"])
    _provenance(doc)
    items = _items(doc)
    assert [(i["reason"], i["text"]) for i in items] == [(UNSOURCED, "9,999")]
    assert items[0]["location"] == {"slide": 1, "shape": "11", "paragraph": 1}


def test_without_provenance_every_number_is_unsourced(tmp_path: Path) -> None:
    doc = _pptx(tmp_path / "a.pptx", ["매출 합계: 10,150"])
    texts = [i["text"] for i in _items(doc)]
    assert texts == ["10,150", "개발팀: 930"]


def test_changed_chart_value_is_unsourced(tmp_path: Path) -> None:
    doc = _pptx(tmp_path / "a.pptx", ["매출 합계: 10,150"], value="931.0")
    _provenance(doc)
    assert [i["text"] for i in _items(doc)] == ["개발팀: 931.0"]


def test_prediction_sentence_without_source_is_flagged() -> None:
    seg = Segment("text", "다음 달도 증가가 예상된다. 합계 10,150", {"slide": 1, "shape": "10"})
    prov = Provenance(text={(1, "10"): [10150.0]})
    items = check_segments([seg], prov)
    assert [(i["reason"], i["text"]) for i in items] == [(UNFOUNDED, "다음 달도 증가가 예상된다.")]


def test_prediction_word_with_sourced_number_is_not_flagged() -> None:
    seg = Segment("text", "최고 매출은 10,150이다", {"slide": 1, "shape": "10"})
    assert check_segments([seg], Provenance(text={(1, "10"): [10150.0]})) == []


def test_prediction_with_unsourced_number_gets_both_reasons() -> None:
    seg = Segment("text", "내년 매출은 20,000으로 전망", {"paragraph": 1})
    reasons = [i["reason"] for i in check_segments([seg], Provenance())]
    assert reasons == [UNFOUNDED, UNSOURCED]


def test_number_glued_to_word_is_not_a_number() -> None:
    seg = Segment("text", "영업1팀과 Q3 보고", {"paragraph": 1})
    assert check_segments([seg], Provenance()) == []


def test_diff_reports_only_changed_parts() -> None:
    a = Segment("text", "가", {"paragraph": 1})
    b = Segment("text", "나", {"paragraph": 2})
    c = Segment("text", "다", {"paragraph": 3})
    assert diff_segments([a, b, c], [a, b, c]) == []
    b2 = Segment("text", "나나", {"paragraph": 2})
    changed = diff_segments([a, b2, c], [a, b, c])
    assert changed == [
        {
            "reason": CHANGED,
            "text": "나나",
            "location": {"paragraph": 2},
            "change": "수정",
            "before": "나",
        }
    ]
    assert [i["change"] for i in diff_segments([a, c], [a, b, c])] == ["삭제"]
    assert [i["change"] for i in diff_segments([a, b, b2, c], [a, b, c])] == ["추가"]


def test_baseline_pptx_chart_change(tmp_path: Path) -> None:
    base = _pptx(tmp_path / "base.pptx", ["제목"])
    new = _pptx(tmp_path / "new.pptx", ["제목"], value="931")
    _provenance(base)
    _provenance(new)
    assert [i for i in _items(base, base) if i["reason"] == CHANGED] == []
    changed = [i for i in _items(new, base) if i["reason"] == CHANGED]
    assert [i["text"] for i in changed] == ["개발팀: 931"]


def test_docx_paragraphs_are_read(tmp_path: Path) -> None:
    doc = tmp_path / "a.docx"
    body = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>매출이 늘어날 것으로 보인다</w:t></w:r></w:p>"
        "<w:p/><w:p><w:r><w:t>회의 3건</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(doc, "w") as z:
        z.writestr("word/document.xml", body)
    assert [s.location for s in read_segments(doc)] == [{"paragraph": 1}, {"paragraph": 3}]
    assert [i["reason"] for i in _items(doc)] == [UNFOUNDED, UNSOURCED]


def test_same_input_same_output(tmp_path: Path) -> None:
    doc = _pptx(tmp_path / "a.pptx", ["예상된다. 비용 9,999", "합계 10,150"])
    first = json.loads(review_map(str(doc)))
    second = json.loads(review_map(str(doc)))
    assert first["items"] == second["items"] and first["items"]


def test_failures_are_returned_not_raised(tmp_path: Path) -> None:
    missing = json.loads(review_map(str(tmp_path / "none.pptx")))
    assert missing["step"] == "open"
    unsupported = json.loads(review_map(str(tmp_path / "a.txt")))
    assert unsupported["step"] == "open"
    broken = tmp_path / "broken.pptx"
    broken.write_bytes(b"not a zip")
    assert json.loads(review_map(str(broken)))["step"] == "read"


def test_broken_provenance_is_reported(tmp_path: Path) -> None:
    doc = _pptx(tmp_path / "a.pptx", ["합계 10,150"])
    doc.with_name(doc.name + ".provenance.json").write_text("{", encoding="utf-8")
    result = json.loads(review_map(str(doc)))
    assert "provenance_error" in result and result["items"]
