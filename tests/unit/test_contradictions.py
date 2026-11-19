"""find_contradictions 단위 테스트 (FR-40)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from docmill.tools.contradictions import find_contradictions

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _docx(path: Path, blocks: list[str | list[list[str]]]) -> Path:
    parts = []
    for b in blocks:
        if isinstance(b, str):
            parts.append(f"<w:p><w:r><w:t>{b}</w:t></w:r></w:p>")
        else:
            rows = "".join(
                "<w:tr>"
                + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row)
                + "</w:tr>"
                for row in b
            )
            parts.append(f"<w:tbl>{rows}</w:tbl>")
    xml = f'<w:document xmlns:w="{_W}"><w:body>{"".join(parts)}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)
    return path


def _pptx(path: Path, slides: list[list[str]], table: list[list[str]] | None = None) -> Path:
    n = len(slides)
    ids = "".join(f'<p:sldId id="{256 + i}" r:id="rId{i}"/>' for i in range(1, n + 1))
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="{_R}/slide" Target="slides/slide{i}.xml"/>'
        for i in range(1, n + 1)
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "ppt/presentation.xml",
            f'<p:presentation xmlns:p="{_P}" xmlns:r="{_R}"><p:sldIdLst>{ids}</p:sldIdLst>'
            "</p:presentation>",
        )
        z.writestr(
            "ppt/_rels/presentation.xml.rels",
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>",
        )
        for i, paras in enumerate(slides, start=1):
            body = "".join(f"<a:p><a:r><a:t>{p}</a:t></a:r></a:p>" for p in paras)
            tbl = ""
            if table and i == 1:
                rows = "".join(
                    "<a:tr>"
                    + "".join(
                        f"<a:tc><a:txBody><a:p><a:r><a:t>{c}</a:t></a:r></a:p></a:txBody></a:tc>"
                        for c in r
                    )
                    + "</a:tr>"
                    for r in table
                )
                tbl = f"<p:graphicFrame><a:tbl>{rows}</a:tbl></p:graphicFrame>"
            z.writestr(
                f"ppt/slides/slide{i}.xml",
                f'<p:sld xmlns:p="{_P}" xmlns:a="{_A}"><p:cSld><p:spTree>'
                f"<p:sp><p:txBody>{body}</p:txBody></p:sp>{tbl}</p:spTree></p:cSld></p:sld>",
            )
    return path


def _run(doc: Path) -> dict[str, object]:
    return dict(json.loads(find_contradictions(str(doc))))


def _items(doc: Path, kind: str) -> list[dict[str, object]]:
    found = _run(doc)["contradictions"]
    assert isinstance(found, list)
    return [c for c in found if c["kind"] == kind]


def test_total_row_mismatch_reports_cell_and_value(tmp_path: Path) -> None:
    table = [["부서", "매출"], ["가", "100"], ["나", "200"], ["합계", "350"]]
    [item] = _items(_docx(tmp_path / "a.docx", [table]), "합계 불일치")
    assert item["expected"] == "300" and item["actual"] == "350"
    assert item["locations"][0]["location"] == "표 1 4행 2열"  # type: ignore[index]


def test_total_column_mismatch_and_thousand_separator(tmp_path: Path) -> None:
    table = [
        ["부서", "1분기", "2분기", "합계"],
        ["가", "1,000", "500", "1,500"],
        ["나", "1", "2", "9"],
    ]
    [item] = _items(_docx(tmp_path / "a.docx", [table]), "합계 불일치")
    assert item["expected"] == "3" and item["actual"] == "9"
    assert item["locations"][0]["location"] == "표 1 3행 4열"  # type: ignore[index]


def test_consistent_total_gives_no_finding(tmp_path: Path) -> None:
    table = [["부서", "매출"], ["가", "100"], ["나", "200"], ["합계", "300"]]
    assert _run(_docx(tmp_path / "a.docx", [table]))["contradictions"] == []


def test_prose_decrease_and_matching_claims(tmp_path: Path) -> None:
    table = [["구분", "전", "후"], ["매출", "200", "150"]]
    doc = _docx(tmp_path / "a.docx", ["매출이 10% 감소했다.", "매출이 25% 감소했다.", table])
    [item] = _items(doc, "비율 불일치")
    assert item["expected"] == "25%" and item["actual"] == "10%"


def test_ambiguous_percent_is_not_reported(tmp_path: Path) -> None:
    table = [["구분", "전", "후"], ["매출", "200", "250"], ["비용", "100", "120"]]
    doc = _docx(tmp_path / "a.docx", ["이익이 30% 증가했다.", table])
    assert _items(doc, "비율 불일치") == []


def test_item_mismatch_across_slides(tmp_path: Path) -> None:
    doc = _pptx(tmp_path / "a.pptx", [["가팀 매출은 10원이다."], ["가팀 매출은 12원이다."]])
    [item] = _items(doc, "항목 불일치")
    places = [e["location"] for e in item["locations"]]  # type: ignore[attr-defined]
    assert places == ["슬라이드 1 문단 1", "슬라이드 2 문단 1"]


def test_pptx_table_total(tmp_path: Path) -> None:
    table = [["항목", "값"], ["a", "1"], ["b", "2"], ["합계", "4"]]
    doc = _pptx(tmp_path / "a.pptx", [["x"]], table)
    [item] = _items(doc, "합계 불일치")
    assert item["locations"][0]["location"] == "슬라이드 1 표 1 4행 2열"  # type: ignore[index]


def test_errors_are_returned_not_raised(tmp_path: Path) -> None:
    bad = tmp_path / "x.txt"
    bad.write_text("a")
    out = _run(bad)
    assert out["step"] == "open" and out["contradictions"] == []
    assert _run(tmp_path / "none.docx")["step"] == "open"
    broken = tmp_path / "b.docx"
    broken.write_text("not a zip")
    assert _run(broken)["step"] == "read"


def test_units_are_converted_in_totals(tmp_path: Path) -> None:
    table = [["항목", "값"], ["a", "1억원"], ["b", "5000만원"], ["합계", "15000만원"]]
    assert _items(_docx(tmp_path / "a.docx", [table]), "합계 불일치") == []
    bad = [["항목", "값"], ["a", "1억원"], ["b", "5000만원"], ["합계", "16000만원"]]
    [item] = _items(_docx(tmp_path / "b.docx", [bad]), "합계 불일치")
    assert item["expected"] == "15000만원" and item["actual"] == "16000만원"


def test_subtotal_sums_only_its_own_section(tmp_path: Path) -> None:
    rows = [["항목", "값"], ["A", "100"], ["B", "200"], ["소계", "300"]]
    rows += [["C", "400"], ["D", "500"], ["소계", "900"], ["합계", "1200"]]
    assert _items(_docx(tmp_path / "a.docx", [rows]), "합계 불일치") == []
    rows[-2][1] = "1200"
    [item] = _items(_docx(tmp_path / "b.docx", [rows]), "합계 불일치")
    assert item["actual"] == "1200" and item["expected"] == "900"


def test_percent_point_claim_is_compared_with_difference(tmp_path: Path) -> None:
    table = [["구분", "전", "후"], ["점유율", "20%", "25%"]]
    ok = _docx(tmp_path / "a.docx", ["점유율은 5%p 증가했다.", table])
    assert _items(ok, "비율 불일치") == []
    bad = _docx(tmp_path / "b.docx", ["점유율은 8%p 증가했다.", table])
    [item] = _items(bad, "비율 불일치")
    assert item["expected"] == "5%p" and item["actual"] == "8%p"


def test_claim_about_other_item_is_not_compared(tmp_path: Path) -> None:
    table = [["구분", "전", "후"], ["매출", "100", "125"]]
    doc = _docx(tmp_path / "a.docx", ["인원은 30% 증가했다.", table])
    assert _items(doc, "비율 불일치") == []


def test_full_item_names_are_kept(tmp_path: Path) -> None:
    doc = _pptx(tmp_path / "a.pptx", [["국내 영업1팀 매출은 10원"], ["해외 영업1팀 매출은 12원"]])
    assert _items(doc, "항목 불일치") == []
    same = _pptx(tmp_path / "b.pptx", [["국내 영업1팀 매출은 10원"], ["국내 영업1팀 매출은 12원"]])
    assert len(_items(same, "항목 불일치")) == 1


def test_item_values_are_unit_converted_but_unit_families_differ(tmp_path: Path) -> None:
    same = _pptx(tmp_path / "a.pptx", [["매출은 1억원이다."], ["매출은 10000만원이다."]])
    assert _items(same, "항목 불일치") == []
    other = _pptx(tmp_path / "b.pptx", [["규모는 10원이다."], ["규모는 10명이다."]])
    assert len(_items(other, "항목 불일치")) == 1


def test_same_item_in_slide_tables_is_compared(tmp_path: Path) -> None:
    def cell(text: str) -> str:
        return f"<a:tc><a:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></a:txBody></a:tc>"

    path = _pptx(tmp_path / "a.pptx", [["x"], ["y"]], [["항목", "값"], ["매출", "100"]])
    rows = f"<a:tr>{cell('항목')}{cell('값')}</a:tr><a:tr>{cell('매출')}{cell('120')}</a:tr>"
    xml = (
        f'<p:sld xmlns:p="{_P}" xmlns:a="{_A}"><p:cSld><p:spTree><p:graphicFrame>'
        f"<a:tbl>{rows}</a:tbl></p:graphicFrame></p:spTree></p:cSld></p:sld>"
    )
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    files["ppt/slides/slide2.xml"] = xml.encode()
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    [item] = _items(path, "항목 불일치")
    places = [e["location"] for e in item["locations"]]  # type: ignore[attr-defined]
    assert places == ["슬라이드 1 표 1 2행 2열", "슬라이드 2 표 1 2행 2열"]


def test_zero_base_percent_point_claim_is_compared(tmp_path: Path) -> None:
    table = [["구분", "전", "후"], ["점유율", "0%", "5%"]]
    ok = _docx(tmp_path / "a.docx", ["점유율은 5%p 증가했다.", table])
    assert _items(ok, "비율 불일치") == []
    bad = _docx(tmp_path / "b.docx", ["점유율은 8%p 증가했다.", table])
    [item] = _items(bad, "비율 불일치")
    assert item["expected"] == "5%p" and item["actual"] == "8%p"


def test_two_column_tables_keep_period_in_item_key(tmp_path: Path) -> None:
    a = [["항목", "2024년"], ["매출", "100"]]
    b = [["항목", "2025년"], ["매출", "125"]]
    assert _items(_docx(tmp_path / "a.docx", [a, b]), "항목 불일치") == []
    c = [["항목", "값"], ["매출", "100"]]
    d = [["항목", "값"], ["매출", "125"]]
    assert len(_items(_docx(tmp_path / "b.docx", [c, d]), "항목 불일치")) == 1
