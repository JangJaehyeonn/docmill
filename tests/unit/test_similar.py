"""비슷한 문서 찾기 단위 테스트 (FR-48)."""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from docmill.tools import similar
from docmill.tools.hwp import HwpError
from docmill.tools.similar import document_title, find_similar_documents, similarity

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _docx(path: Path, first: str) -> None:
    xml = f"<w:document><w:body><w:p><w:r><w:t>{first}</w:t></w:r></w:p></w:body></w:document>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)


def test_similarity_rules() -> None:
    assert similarity("주간 보고", "주간보고") == 1.0
    assert similarity("주간 보고", "회의록") == 0.0
    assert similarity("", "x") == 0.0


def test_recent_first_and_stable(tmp_path: Path) -> None:
    for name, mtime in (("a.docx", 100), ("b.docx", 300), ("c.docx", 200)):
        _docx(tmp_path / name, "주간 보고")
        os.utime(tmp_path / name, (mtime, mtime))
    _docx(tmp_path / "m.docx", "회의록")
    out = find_similar_documents(str(tmp_path), "주간 보고")
    assert [i["path"] for i in json.loads(out)["similar"]] == ["b.docx", "c.docx", "a.docx"]
    assert find_similar_documents(str(tmp_path), "주간 보고") == out


def _xlsx(path: Path, first_cell: str, core_title: str | None = None) -> None:
    book = f'<workbook xmlns="{_S}" xmlns:r="{_R}"><sheets><sheet name="S" r:id="rId1"/></sheets></workbook>'
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>'
    )
    sheet = (
        f'<worksheet xmlns="{_S}"><sheetData><row r="1"><c r="A1"/>'
        f'<c r="B1" t="inlineStr"><is><t>{first_cell}</t></is></c></row></sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", book)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        if core_title is not None:
            z.writestr(
                "docProps/core.xml",
                '<cp:coreProperties xmlns:cp="x" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                f"<dc:title>{core_title}</dc:title></cp:coreProperties>",
            )


def test_xlsx_title_from_first_cell_or_core_title(tmp_path: Path) -> None:
    _xlsx(tmp_path / "data1.xlsx", "주간 보고 집계")
    _xlsx(tmp_path / "data2.xlsx", "매출", core_title="주간 보고")
    _xlsx(tmp_path / "other.xlsx", "회의록")
    assert document_title(tmp_path / "data1.xlsx") == "주간 보고 집계"
    assert document_title(tmp_path / "data2.xlsx") == "주간 보고"
    out = json.loads(find_similar_documents(str(tmp_path), "주간 보고"))
    assert sorted(i["path"] for i in out["similar"]) == ["data1.xlsx", "data2.xlsx"]


def test_broken_xlsx_has_empty_title(tmp_path: Path) -> None:
    (tmp_path / "broken.xlsx").write_bytes(b"not a zip")
    assert document_title(tmp_path / "broken.xlsx") == ""


def test_hwpx_title_from_first_paragraph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_lines(path: Path) -> list[str]:
        if path.name == "broken.hwpx":
            raise HwpError("읽기 실패")
        return ["", "  주간 보고  ", "본문"] if path.name == "doc1.hwpx" else ["회의록"]

    monkeypatch.setattr(similar, "read_hwp_lines", fake_lines)
    for name in ("doc1.hwpx", "doc2.hwpx", "broken.hwpx"):
        (tmp_path / name).write_bytes(b"")
    assert document_title(tmp_path / "broken.hwpx") == ""
    out = json.loads(find_similar_documents(str(tmp_path), "주간 보고"))
    assert [(i["path"], i["title"]) for i in out["similar"]] == [("doc1.hwpx", "주간 보고")]


def test_hwp_title_from_real_document(tmp_path: Path) -> None:
    shutil.copyfile(FIXTURES / "meeting_1111.hwp", tmp_path / "x.hwp")
    assert document_title(tmp_path / "x.hwp").startswith("주간 회의록")
