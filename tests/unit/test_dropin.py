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
    found = _numbers([[0.004]], (1, 1), (Path("a.xlsx"), "S", "sha256:x"), 1, 3)
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


_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _two_sheet_xlsx(path: Path, first: float, second: float) -> Path:
    """`요약`(B2=first)과 `3분기`(B2=second) 두 시트가 있는 최소 엑셀 파일."""

    def sheet(value: float) -> str:
        return (
            f'<worksheet xmlns="{_MAIN}"><sheetData><row r="2">'
            f'<c r="B2"><v>{value}</v></c></row></sheetData></worksheet>'
        )

    files = {
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/></Types>'
        ),
        "_rels/.rels": (
            f'<Relationships xmlns="{_PKG_REL}"><Relationship Id="rId1" '
            f'Type="{_DOC_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            f'<workbook xmlns="{_MAIN}" xmlns:r="{_DOC_REL}"><sheets>'
            '<sheet name="요약" sheetId="1" r:id="rId1"/>'
            '<sheet name="3분기" sheetId="2" r:id="rId2"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<Relationships xmlns="{_PKG_REL}">'
            f'<Relationship Id="rId1" Type="{_DOC_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId2" Type="{_DOC_REL}/worksheet" Target="worksheets/sheet2.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": sheet(first),
        "xl/worksheets/sheet2.xml": sheet(second),
    }
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return path


def test_uses_selected_sheet(tmp_path: Path) -> None:
    pptx, _ = _workspace(tmp_path)
    book = _two_sheet_xlsx(tmp_path / "book.xlsx", 10, 120)
    result = json.loads(insert_range(str(pptx), str(book), "B2", "3분기"))
    assert result["sheet"] == "3분기"
    with zipfile.ZipFile(pptx) as z:
        assert "<a:t>120</a:t>" in z.read("ppt/slides/slide4.xml").decode("utf-8")
    numbers = json.loads(provenance_path(pptx).read_text(encoding="utf-8"))["numbers"]
    assert numbers[0]["value"] == 120 and numbers[0]["source"]["sheet"] == "3분기"
    # without a sheet the first sheet is used, as before
    assert json.loads(insert_range(str(pptx), str(book), "B2"))["sheet"] == "요약"
    numbers = json.loads(provenance_path(pptx).read_text(encoding="utf-8"))["numbers"]
    assert numbers[1]["value"] == 10 and numbers[1]["source"]["sheet"] == "요약"


def test_unknown_sheet_is_refused(tmp_path: Path) -> None:
    pptx, _ = _workspace(tmp_path)
    before = pptx.read_bytes()
    book = _two_sheet_xlsx(tmp_path / "book.xlsx", 10, 120)
    result = json.loads(insert_range(str(pptx), str(book), "B2", "없는 시트"))
    assert result["step"] == "source" and pptx.read_bytes() == before


def test_cli_sheet_option(tmp_path: Path) -> None:
    pptx, _ = _workspace(tmp_path)
    book = _two_sheet_xlsx(tmp_path / "book.xlsx", 10, 120)
    out = CliRunner().invoke(app, ["insert-range", str(pptx), str(book), "B2", "--sheet", "3분기"])
    assert out.exit_code == 0
    assert json.loads(out.stdout.strip().splitlines()[-1])["sheet"] == "3분기"


def test_prefixed_content_types_are_linked(tmp_path: Path) -> None:
    pptx, xlsx = _workspace(tmp_path)
    ct = "http://schemas.openxmlformats.org/package/2006/content-types"
    with zipfile.ZipFile(pptx) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(pptx, "w") as z:
        for info, data in items:
            if info.filename == "[Content_Types].xml":
                text = data.decode("utf-8").replace(f'xmlns="{ct}"', f'xmlns:ct="{ct}"')
                for tag in ("Types", "Default", "Override"):
                    text = text.replace(f"<{tag}", f"<ct:{tag}").replace(
                        f"</{tag}>", f"</ct:{tag}>"
                    )
                data = text.encode("utf-8")
            z.writestr(info, data)
    assert "error" not in _run(pptx, xlsx)
    with zipfile.ZipFile(pptx) as z:
        types = z.read("[Content_Types].xml").decode("utf-8")
    assert '<ct:Override PartName="/ppt/slides/slide4.xml"' in types


def _digest(path: Path) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_records_version_of_the_bytes_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import docmill.tools.dropin as dropin

    pptx, xlsx = _workspace(tmp_path)
    read = _digest(xlsx)
    changed = _two_sheet_xlsx(tmp_path / "other.xlsx", 1, 2).read_bytes()
    publish = dropin._publish_pair

    def source_saved_meanwhile(*args: Any) -> None:
        xlsx.write_bytes(changed)  # the user saves Excel after the read, before publishing
        publish(*args)

    monkeypatch.setattr(dropin, "_publish_pair", source_saved_meanwhile)
    assert "error" not in _run(pptx, xlsx, "C2")
    numbers = json.loads(provenance_path(pptx).read_text(encoding="utf-8"))["numbers"]
    assert numbers[0]["source"]["read_version"] == read != _digest(xlsx)


def test_refresh_updates_read_version(tmp_path: Path) -> None:
    pptx, xlsx = _workspace(tmp_path)
    _run(pptx, xlsx, "C2")
    data = xlsx.read_bytes()
    with zipfile.ZipFile(xlsx) as zin:
        items = [(i, zin.read(i.filename)) for i in zin.infolist()]
    with zipfile.ZipFile(xlsx, "w") as zout:
        for info, raw in items:
            if info.filename == "xl/worksheets/sheet1.xml":
                raw = raw.replace(b'<c r="C2" t="n"><v>1200</v>', b'<c r="C2" t="n"><v>1500</v>')
            zout.writestr(info, raw)
    assert xlsx.read_bytes() != data
    assert [c["new"] for c in json.loads(refresh_links(str(pptx)))["changes"]] == [1500]
    numbers = json.loads(provenance_path(pptx).read_text(encoding="utf-8"))["numbers"]
    assert numbers[0]["source"]["read_version"] == _digest(xlsx)


_KILL_BETWEEN_PUBLISHES = """
import os, sys
import docmill.tools.refresh as refresh
from docmill.tools.dropin import insert_range

real = refresh._publish

def publish(data, final):
    if final.name.endswith(".provenance.json"):
        os._exit(9)  # hard kill: the document is published, the provenance is not
    real(data, final)

refresh._publish = publish
insert_range(sys.argv[1], sys.argv[2], "A1:C3")
"""


def test_killed_insert_is_rolled_back_next_time(tmp_path: Path) -> None:
    import subprocess
    import sys

    pptx, xlsx = _workspace(tmp_path)
    _run(pptx, xlsx, "C4")
    before_doc = pptx.read_bytes()
    before_prov = provenance_path(pptx).read_bytes()
    killed = subprocess.run(
        [sys.executable, "-c", _KILL_BETWEEN_PUBLISHES, str(pptx), str(xlsx)], check=False
    )
    assert killed.returncode == 9
    assert pptx.read_bytes() != before_doc  # half published
    assert provenance_path(pptx).read_bytes() == before_prov
    result = _run(pptx, xlsx, "A1:C3")  # retry recovers first, then inserts once
    assert result["slide"] == 5
    numbers = json.loads(provenance_path(pptx).read_text(encoding="utf-8"))["numbers"]
    assert [n["location"]["slide"] for n in numbers] == [4, 5, 5]
    with zipfile.ZipFile(pptx) as z:
        assert "ppt/slides/slide6.xml" not in z.namelist()


def test_completed_insert_is_not_rolled_back(tmp_path: Path) -> None:
    from docmill.tools.dropin import _journal, _recover

    pptx, xlsx = _workspace(tmp_path)
    _run(pptx, xlsx, "C4")
    assert not _journal(pptx).exists()
    after = pptx.read_bytes()
    _recover(pptx)
    assert pptx.read_bytes() == after


def test_refresh_recovers_interrupted_insert_first(tmp_path: Path) -> None:
    import subprocess
    import sys

    pptx, xlsx = _workspace(tmp_path)
    _run(pptx, xlsx, "C4")
    before_doc = pptx.read_bytes()
    killed = subprocess.run(
        [sys.executable, "-c", _KILL_BETWEEN_PUBLISHES, str(pptx), str(xlsx)], check=False
    )
    assert killed.returncode == 9
    assert pptx.read_bytes() != before_doc  # half published
    assert "error" not in json.loads(refresh_links(str(pptx)))
    assert pptx.read_bytes() == before_doc  # refresh rolled the half insert back first


def test_refresh_uses_recorded_cell_not_same_text(tmp_path: Path) -> None:
    pptx, xlsx = _workspace(tmp_path)
    _run(pptx, xlsx)
    with zipfile.ZipFile(pptx) as zin:
        items = [(i, zin.read(i.filename)) for i in zin.infolist()]
    with zipfile.ZipFile(pptx, "w") as zout:
        for info, raw in items:
            if info.filename == "ppt/slides/slide4.xml":
                # a text cell before the numeric cell shows the same digits as the linked number
                text = raw.decode("utf-8")
                at = text.index("<a:t>") + len("<a:t>")
                raw = (text[:at] + "1200" + text[text.index("</a:t>", at) :]).encode("utf-8")
            zout.writestr(info, raw)
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
    assert [c["new"] for c in json.loads(refresh_links(str(pptx)))["changes"]] == [1500]
    with zipfile.ZipFile(pptx) as z:
        slide = z.read("ppt/slides/slide4.xml").decode("utf-8")
    assert "<a:t>1200</a:t>" in slide and "1,500" in slide


def _set_b2(book: Path, value: str) -> None:
    """첫 시트 B2 값을 바꾼다."""
    with zipfile.ZipFile(book) as z:
        files = {i.filename: z.read(i.filename) for i in z.infolist()}
    sheet1 = files["xl/worksheets/sheet1.xml"].decode()
    head, rest = sheet1.split('<c r="B2"><v>', 1)
    files["xl/worksheets/sheet1.xml"] = (
        head + f'<c r="B2"><v>{value}' + rest[rest.index("</v>") :]
    ).encode()
    with zipfile.ZipFile(book, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)


def test_tiny_decimal_survives_insert_and_refresh(tmp_path: Path) -> None:
    pptx, _ = _workspace(tmp_path)
    book = _two_sheet_xlsx(tmp_path / "book.xlsx", 0, 0)
    _set_b2(book, "0.0000004")
    assert "error" not in json.loads(insert_range(str(pptx), str(book), "B2"))
    with zipfile.ZipFile(pptx) as z:
        assert "<a:t>0.0000004</a:t>" in z.read("ppt/slides/slide4.xml").decode("utf-8")
    number = json.loads(provenance_path(pptx).read_text(encoding="utf-8"))["numbers"][0]
    assert number["value"] == 4e-07 and number["source"]["digits"] == 7
    _set_b2(book, "0.0000005")
    result = json.loads(refresh_links(str(pptx)))
    assert [c["new"] for c in result["changes"]] == [5e-07]
    with zipfile.ZipFile(pptx) as z:
        assert "<a:t>0.0000005</a:t>" in z.read("ppt/slides/slide4.xml").decode("utf-8")
    number = json.loads(provenance_path(pptx).read_text(encoding="utf-8"))["numbers"][0]
    assert number["value"] == 5e-07 and number["source"]["digits"] == 7


def test_report_numbers_keep_report_digits() -> None:
    from docmill.tools.refresh import _shown

    src = {"aggregation": "sumif", "digits": 2}
    assert _shown(src, 1234.567) == (1234.57, {**src, "digits": 2})
    assert _shown({**src, "literal": True}, 1234.567)[0] == 1234.567


def test_long_cells_grow_rows_and_still_fit() -> None:
    from docmill.tools.dropin import _BOTTOM, _TOP, _layout, _row_heights

    grid: list[list[Any]] = [["긴 설명 " * 12, 1200] for _ in range(10)]
    font, heights = _layout(grid, 4114800)
    single = _row_heights([["짧음", 1]], 4114800, font)[0]
    assert all(h > single for h in heights)  # 줄바꿈된 줄 수만큼 행이 높다
    assert sum(heights) <= _BOTTOM - _TOP


def test_too_long_cells_are_refused() -> None:
    from docmill.tools.dropin import DropInError, _layout

    with pytest.raises(DropInError) as err:
        _layout([["아주 긴 설명 " * 40] for _ in range(30)], 8229600)
    assert err.value.step == "range"
