"""insert_range (FR-44) 단위 테스트. 엔진 없이 저장소 샘플 파일(tests/fixtures)을 쓴다."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from docmill.cli import app
from docmill.tools.dropin import insert_range, parse_range
from docmill.tools.provenance import provenance_path
from docmill.tools.refresh import refresh_links

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    shutil.copy(FIXTURES / "sales_q3.xlsx", tmp_path / "sales_q3.xlsx")
    shutil.copy(FIXTURES / "company_intro.pptx", tmp_path / "company_intro.pptx")
    return tmp_path / "company_intro.pptx", tmp_path / "sales_q3.xlsx"


def _run(pptx: Path, xlsx: Path, cell_range: str = "A1:C16") -> dict[str, Any]:
    return dict(json.loads(insert_range(str(pptx), str(xlsx), cell_range)))


def test_parse_range() -> None:
    assert parse_range("A1:C16") == (1, 1, 16, 3)
    assert parse_range("$b2") == (2, 2, 2, 2)


@pytest.mark.parametrize(
    "bad", ["", "A1:", "C3:A1", "A5:A1", "A1:A500", "A1:ZZ2", "x", "A1048577", "XFE1"]
)
def test_parse_range_rejects(bad: str) -> None:
    from docmill.tools.dropin import DropInError

    with pytest.raises(DropInError):
        parse_range(bad)


def test_adds_table_slide_with_provenance(tmp_path: Path) -> None:
    pptx, xlsx = _workspace(tmp_path)
    result = _run(pptx, xlsx)
    assert result["slide"] == 4 and result["numbers"] == 15
    with zipfile.ZipFile(pptx) as z:
        slide = z.read("ppt/slides/slide4.xml").decode("utf-8")
        assert z.testzip() is None
        assert "slide4.xml" in z.read("ppt/_rels/presentation.xml.rels").decode()
        assert "/ppt/slides/slide4.xml" in z.read("[Content_Types].xml").decode()
        assert 'r:id="rId10"' in z.read("ppt/presentation.xml").decode()
    assert slide.count("<a:tbl>") == 1 and slide.count("<a:tr ") == 16
    assert "1,200" in slide
    numbers = json.loads(provenance_path(pptx).read_text(encoding="utf-8"))["numbers"]
    assert numbers[0]["value"] == 1200
    assert numbers[0]["source"]["range"] == "C2:C2"
    assert numbers[0]["source"]["file"] == str(xlsx)
    assert numbers[0]["location"]["slide"] == 4


def test_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    pptx1, xlsx1 = _workspace(first)
    pptx2, xlsx2 = _workspace(second)
    assert "error" not in _run(pptx1, xlsx1)
    assert "error" not in _run(pptx2, xlsx2)
    assert pptx1.read_bytes() == pptx2.read_bytes()


def test_appends_to_existing_provenance(tmp_path: Path) -> None:
    pptx, xlsx = _workspace(tmp_path)
    _run(pptx, xlsx, "A1:C3")
    _run(pptx, xlsx, "C4")
    numbers = json.loads(provenance_path(pptx).read_text(encoding="utf-8"))["numbers"]
    assert [n["value"] for n in numbers] == [1200, 980, 450]
    assert [n["location"]["slide"] for n in numbers] == [4, 4, 5]


def test_refresh_links_fixes_dropped_table(tmp_path: Path) -> None:
    pptx, xlsx = _workspace(tmp_path)
    _run(pptx, xlsx)
    changed = tmp_path / "changed.xlsx"
    with zipfile.ZipFile(xlsx) as zin, zipfile.ZipFile(changed, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(
                    b'<c r="C2" t="n"><v>1200</v>', b'<c r="C2" t="n"><v>1500</v>', 1
                )
            zout.writestr(item, data)
    shutil.copy(changed, xlsx)
    result = json.loads(refresh_links(str(pptx)))
    assert [c["new"] for c in result["changes"]] == [1500]
    with zipfile.ZipFile(pptx) as z:
        assert "1,500" in z.read("ppt/slides/slide4.xml").decode("utf-8")


def test_refresh_keeps_dropped_cell_literal_next_to_report_pair(tmp_path: Path) -> None:
    from docmill.tools.refresh import _renewed, _Sources

    pptx, xlsx = _workspace(tmp_path)
    _run(pptx, xlsx, "C2")
    dropped = json.loads(provenance_path(pptx).read_text(encoding="utf-8"))["numbers"][0]
    report = json.loads(json.dumps(dropped))
    report["source"].pop("literal")
    report["source"]["criteria_range"] = "Z2:Z2"  # blank names: the pair path would total 0
    sources = _Sources(pptx, [report, dropped])
    assert _renewed(dropped, sources)[1] == 1200


def test_failures_leave_document_untouched(tmp_path: Path) -> None:
    pptx, xlsx = _workspace(tmp_path)
    before = pptx.read_bytes()
    assert _run(pptx, xlsx, "bad")["step"] == "range"
    assert _run(pptx, tmp_path / "missing.xlsx")["step"] == "input"
    docx = tmp_path / "a.docx"
    docx.write_bytes(b"x")
    assert _run(docx, xlsx)["step"] == "input"
    assert pptx.read_bytes() == before
    assert not provenance_path(pptx).exists()


def test_cli_command(tmp_path: Path) -> None:
    pptx, xlsx = _workspace(tmp_path)
    out = CliRunner().invoke(app, ["insert-range", str(pptx), str(xlsx), "A1:C16"])
    assert out.exit_code == 0
    assert json.loads(out.stdout.strip().splitlines()[-1])["slide"] == 4


def test_parse_range_accepts_last_sheet_cell() -> None:
    assert parse_range("XFD1048576") == (1048576, 16384, 1048576, 16384)


def test_small_decimals_keep_their_value() -> None:
    from docmill.tools.dropin import _cell_text, _numbers

    assert _cell_text(0.004) == "0.004"
    assert _cell_text(1200) == "1,200"
    assert _cell_text(1200.5) == "1,200.50"
    found = _numbers([[0.004]], (1, 1), Path("a.xlsx"), "S", 1, 3)
    assert found[0]["value"] == 0.004 and found[0]["source"]["digits"] == 3


def test_tall_table_fits_slide() -> None:
    from docmill.tools.dropin import _BOTTOM, _CELL_MARGIN, _MIN_FONT, _TOP, MAX_ROWS, _font_size

    for rows in (1, 16, MAX_ROWS):
        font = _font_size(rows)
        assert font >= _MIN_FONT
        assert rows * (font / 100 * 12700 * 1.2 + 2 * _CELL_MARGIN) <= _BOTTOM - _TOP


def test_prefixed_relationships_are_linked(tmp_path: Path) -> None:
    pptx, xlsx = _workspace(tmp_path)
    with zipfile.ZipFile(pptx) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(pptx, "w") as z:
        for info, data in items:
            if info.filename == "ppt/_rels/presentation.xml.rels":
                text = data.decode("utf-8")
                text = text.replace("<Relationships", "<rel:Relationships")
                text = text.replace("</Relationships>", "</rel:Relationships>")
                text = text.replace("<Relationship ", "<rel:Relationship ")
                data = text.replace(
                    'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"',
                    'xmlns:rel="http://schemas.openxmlformats.org/package/2006/relationships"',
                ).encode("utf-8")
            z.writestr(info, data)
    assert "error" not in _run(pptx, xlsx)
    with zipfile.ZipFile(pptx) as z:
        assert b"slides/slide4.xml" in z.read("ppt/_rels/presentation.xml.rels")


def test_provenance_source_is_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pptx, xlsx = _workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert "error" not in _run(Path(pptx.name), Path(xlsx.name))
    numbers = json.loads(provenance_path(pptx).read_text(encoding="utf-8"))["numbers"]
    assert Path(numbers[0]["source"]["file"]).is_absolute()


def test_cli_error_is_logged_as_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import docmill.runlog as runlog

    recorded: list[str] = []
    monkeypatch.setattr(runlog, "_safe_record", lambda **kw: recorded.append(kw["status"]))
    pptx, xlsx = _workspace(tmp_path)
    out = CliRunner().invoke(app, ["insert-range", str(pptx), str(xlsx), "bad"])
    assert json.loads(out.stdout.strip().splitlines()[-1])["step"] == "range"
    assert recorded == ["error"]
