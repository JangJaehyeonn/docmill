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
    assert len(changes) == 3


def test_errors_are_reported(tmp_path: Path) -> None:
    a = _docx(tmp_path / "a.docx", ["x"])
    assert _run(a, tmp_path / "none.docx")["step"] == "open"
    other = tmp_path / "b.txt"
    other.write_text("x")
    assert _run(a, other)["step"] == "open"
