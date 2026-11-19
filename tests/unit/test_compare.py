"""compare_versions 단위 테스트 (FR-41)."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from docmill.tools.compare import compare_versions

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _docx(path: Path, paras: list[str], cells: list[str] | None = None) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paras)
    if cells:
        row = "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in cells)
        body += f"<w:tbl><w:tr>{row}</w:tr></w:tbl>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "word/document.xml", f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
        )
    return path


def _xlsx(path: Path, values: dict[str, str]) -> Path:
    rows = "".join(f'<row r="1"><c r="{r}"><v>{v}</v></c></row>' for r, v in values.items())
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>'
    )
    book = (
        f'<workbook xmlns="{_S}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="S" r:id="rId1"/></sheets></workbook>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", book)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{_S}"><sheetData>{rows}</sheetData></worksheet>',
        )
    return path


def _run(before: Path, after: Path) -> dict[str, Any]:
    return dict(json.loads(compare_versions(str(before), str(after))))


def test_numbers_and_sentence_and_cells(tmp_path: Path) -> None:
    a = _docx(tmp_path / "a.docx", ["매출 1,200만원", "예산을 논의"], ["항목", "10"])
    b = _docx(tmp_path / "b.docx", ["매출 1,500만원", "인력을 논의"], ["항목", "12"])
    changes = _run(a, b)["changes"]
    assert [(c["kind"], c["old"], c["new"]) for c in changes] == [
        ("숫자", "1,200", "1,500"),
        ("문장", "예산을 논의", "인력을 논의"),
        ("숫자", "10", "12"),
    ]
    assert all(c["location"] for c in changes)


def test_added_paragraph_and_identical(tmp_path: Path) -> None:
    a = _docx(tmp_path / "a.docx", ["하나", "둘"])
    b = _docx(tmp_path / "b.docx", ["하나", "새 문단", "둘"])
    changes = _run(a, b)["changes"]
    assert [(c["old"], c["new"]) for c in changes] == [("", "새 문단")]
    copy = tmp_path / "c.docx"
    shutil.copy(a, copy)
    assert _run(a, copy)["changes"] == []


def test_xlsx_cells(tmp_path: Path) -> None:
    a = _xlsx(tmp_path / "a.xlsx", {"A1": "5", "B1": "7"})
    b = _xlsx(tmp_path / "b.xlsx", {"A1": "5", "B1": "9"})
    changes = _run(a, b)["changes"]
    assert changes == [{"kind": "숫자", "location": "S B1", "old": "7", "new": "9"}]


def test_hwp_fixture() -> None:
    changes = _run(FIXTURES / "meeting_1111.hwp", FIXTURES / "meeting_1111_v2.hwp")["changes"]
    assert [(c["kind"], c["location"], c["old"], c["new"]) for c in changes] == [
        (
            "문장",
            "구역 1 문단 4",
            "3분기 매출 보고서와 신규 고객 제안서 일정을 점검했다.",
            "3분기 매출 보고서와 신규 고객 제안서 진행 상황을 점검했다.",
        ),
        ("숫자", "구역 1 문단 6", "20", "21"),
        ("숫자", "구역 1 문단 7", "15", "14"),
    ]


def test_hwp_table_cell() -> None:
    changes = _run(FIXTURES / "table_v1.hwp", FIXTURES / "table_v2.hwp")["changes"]
    assert changes == [
        {"kind": "숫자", "location": "구역 1 문단 2 표 1 2행 2열", "old": "1,200", "new": "1,500"}
    ]


def _pptx(path: Path, texts: list[str]) -> Path:
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    ids = "".join(f'<p:sldId id="{255 + i}" r:id="rId{i}"/>' for i in range(1, len(texts) + 1))
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="{r}/slide" Target="slides/slide{i}.xml"/>'
        for i in range(1, len(texts) + 1)
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="{pkg}"><Relationship Id="rId1" Type="{r}/officeDocument" '
            'Target="ppt/presentation.xml"/></Relationships>',
        )
        z.writestr(
            "ppt/presentation.xml",
            f'<p:presentation xmlns:p="{p}" xmlns:r="{r}"><p:sldIdLst>{ids}</p:sldIdLst>'
            "</p:presentation>",
        )
        z.writestr(
            "ppt/_rels/presentation.xml.rels",
            f'<Relationships xmlns="{pkg}">{rels}</Relationships>',
        )
        for i, text in enumerate(texts, 1):
            z.writestr(
                f"ppt/slides/slide{i}.xml",
                f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree><p:sp><p:txBody>'
                f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>",
            )
    return path


def test_pptx_numbers_and_sentence(tmp_path: Path) -> None:
    a = _pptx(tmp_path / "a.pptx", ["매출 100원", "예산을 논의"])
    b = _pptx(tmp_path / "b.pptx", ["매출 120원", "인력을 논의"])
    changes = _run(a, b)["changes"]
    assert [(c["kind"], c["old"], c["new"]) for c in changes] == [
        ("숫자", "100", "120"),
        ("문장", "예산을 논의", "인력을 논의"),
    ]
    assert all(c["location"] for c in changes)


def test_negative_numbers_keep_sign(tmp_path: Path) -> None:
    a = _docx(tmp_path / "a.docx", ["손익 -10원", "손익 -5원", "날짜 2026-11-20"])
    b = _docx(tmp_path / "b.docx", ["손익 -20원", "손익 5원", "날짜 2026-11-21"])
    changes = _run(a, b)["changes"]
    assert [(c["old"], c["new"]) for c in changes] == [("-10", "-20"), ("-5", "5"), ("20", "21")]


def test_xlsx_dot_relative_target(tmp_path: Path) -> None:
    a = _xlsx(tmp_path / "a.xlsx", {"A1": "1"})
    b = _xlsx(tmp_path / "b.xlsx", {"A1": "2"})
    for path in (a, b):
        with zipfile.ZipFile(path) as z:
            items = {n: z.read(n) for n in z.namelist()}
        items["xl/_rels/workbook.xml.rels"] = items["xl/_rels/workbook.xml.rels"].replace(
            b"worksheets/", b"./worksheets/"
        )
        with zipfile.ZipFile(path, "w") as z:
            for name, data in items.items():
                z.writestr(name, data)
    assert _run(a, b)["changes"] == [{"kind": "숫자", "location": "S A1", "old": "1", "new": "2"}]


def test_one_paragraph_with_two_numbers_and_one_sentence(tmp_path: Path) -> None:
    a = _docx(tmp_path / "a.docx", ["매출 100원이다. 비용 20원이다. 회의를 연다."])
    b = _docx(tmp_path / "b.docx", ["매출 110원이다. 비용 25원이다. 회의를 취소한다."])
    changes = _run(a, b)["changes"]
    assert [(c["kind"], c["old"], c["new"]) for c in changes] == [
        ("숫자", "100", "110"),
        ("숫자", "20", "25"),
        ("문장", "회의를 연다.", "회의를 취소한다."),
    ]


def test_inserted_paragraph_does_not_shift_pairs(tmp_path: Path) -> None:
    a = _docx(tmp_path / "a.docx", ["매출 100", "비용 20"])
    b = _docx(tmp_path / "b.docx", ["신규 문단", "매출 110", "비용 25"])
    changes = _run(a, b)["changes"]
    assert [(c["kind"], c["old"], c["new"]) for c in changes] == [
        ("문장", "", "신규 문단"),
        ("숫자", "100", "110"),
        ("숫자", "20", "25"),
    ]


def test_errors_are_reported(tmp_path: Path) -> None:
    a = _docx(tmp_path / "a.docx", ["x"])
    assert _run(a, tmp_path / "none.docx")["step"] == "open"
    other = tmp_path / "b.txt"
    other.write_text("x")
    assert _run(a, other)["step"] == "open"
