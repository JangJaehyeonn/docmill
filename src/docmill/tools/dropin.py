"""`insert_range` 도구 (FR-44): 엑셀 셀 범위를 pptx 문서에 표로 넣는다.

studio에서 엑셀 탭의 범위를 PPT 탭으로 끌어다 놓으면 이 도구가 불린다. 범위의 셀을 읽어 새 슬라이드
(문서 맨 끝)에 표를 만들고, 숫자 셀마다 원본 출처(파일·시트·셀)를 문서 옆
`<문서 이름>.provenance.json`(FR-21)에 덧붙인다. 출처는 `sum` 집계의 한 칸 범위(`C2:C2`)라서
원본이 바뀌면 `refresh_links`(FR-37)가 이 표의 숫자도 고친다.

문서는 ADR 0024와 같이 패키지를 바이트 단위로 이어 붙여 고친다. 같은 입력이면 같은 결과다
(시각·난수 없음, 슬라이드·도형 번호는 문서에서 정해진다).
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from docmill.runlog import current_run_id, new_run_id
from docmill.tools.ooxml import OoxmlError, Package
from docmill.tools.provenance import column_letter, column_number, dump, provenance_path
from docmill.tools.refresh import (
    RefreshError,
    _commit,
    _document_lock,
    number_digits,
    read_cells,
)

MAX_ROWS = 30
MAX_SHEET_ROW = 1048576
MAX_SHEET_COLUMN = 16384
MAX_COLUMNS = 20
_RANGE = re.compile(r"\$?([A-Za-z]{1,3})\$?(\d+)(?::\$?([A-Za-z]{1,3})\$?(\d+))?")
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_SLIDE_PART = re.compile(r"ppt/slides/slide(\d+)\.xml")
_TABLE_STYLE = "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"
_FONT_SIZE = 1200
_MIN_FONT = 800
_CELL_MARGIN = 18288
_MARGIN = 457200
_TOP = 1600200
_BOTTOM = 6400800


class DropInError(Exception):
    """범위 넣기 단계 하나가 실패했을 때 발생한다. `step`은 실패한 단계 이름이다."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


def parse_range(text: str) -> tuple[int, int, int, int]:
    """`A1:C16`(또는 한 칸 `B2`)을 (시작 행, 시작 열, 끝 행, 끝 열) 번호로 바꾼다."""
    m = _RANGE.fullmatch(text.strip())
    if m is None:
        raise DropInError("range", f"셀 범위를 해석하지 못했다: {text!r} (예: A1:C16)")
    c1 = column_number(m.group(1).upper())
    r1 = int(m.group(2))
    c2 = column_number((m.group(3) or m.group(1)).upper())
    r2 = int(m.group(4) or m.group(2))
    if r1 < 1 or r2 < r1 or c2 < c1:
        raise DropInError("range", f"셀 범위의 시작이 끝보다 뒤에 있다: {text!r}")
    if r2 > MAX_SHEET_ROW or c2 > MAX_SHEET_COLUMN:
        raise DropInError("range", f"엑셀 시트 범위(XFD1048576)를 벗어난 주소다: {text!r}")
    if r2 - r1 + 1 > MAX_ROWS or c2 - c1 + 1 > MAX_COLUMNS:
        raise DropInError(
            "range", f"범위가 너무 크다 (최대 {MAX_ROWS}행 × {MAX_COLUMNS}열): {text!r}"
        )
    return r1, c1, r2, c2


def _first_sheet(xlsx: Path) -> str:
    try:
        parts = Package.read(xlsx.read_bytes()).parts
        sheet = next(ET.fromstring(parts["xl/workbook.xml"]).iter(f"{_S}sheet"), None)
    except (OSError, KeyError, ValueError, OoxmlError, ET.ParseError) as exc:
        raise DropInError("source", f"원본 엑셀을 읽지 못했다: {xlsx} ({exc})") from exc
    if sheet is None or not sheet.get("name"):
        raise DropInError("source", f"원본 엑셀에 시트가 없다: {xlsx}")
    return str(sheet.get("name"))


def _digits(value: float) -> int:
    return number_digits(value)


def _cell_text(value: Any) -> str:
    """표 칸에 쓸 글자. 숫자는 보고서와 같은 형식(`1,200`)으로 쓴다."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return f"{value:,.{_digits(float(value))}f}"
    return str(value).strip()


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _font_size(rows: int) -> int:
    """행이 표 영역에 모두 들어가도록 글꼴 크기(1/100pt)를 정한다 (행 높이 = 글자 높이 + 여백)."""
    fit = ((_BOTTOM - _TOP) // rows - 2 * _CELL_MARGIN) / 12700 / 1.2
    return max(_MIN_FONT, min(_FONT_SIZE, int(fit) * 100))


def _cell_xml(text: str, numeric: bool, font: int) -> str:
    align = ' algn="r"' if numeric else ""
    size = f'lang="ko-KR" sz="{font}"'
    pr = f'<a:tcPr marT="{_CELL_MARGIN}" marB="{_CELL_MARGIN}"/>'
    if not text:
        empty = f"<a:p><a:endParaRPr {size}/></a:p>"
        return f"<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>{empty}</a:txBody>{pr}</a:tc>"
    run = f"<a:r><a:rPr {size}/><a:t>{escape(text)}</a:t></a:r>"
    para = f"<a:p><a:pPr{align}/>{run}</a:p>"
    return f"<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>{para}</a:txBody>{pr}</a:tc>"


def _table_xml(grid: list[list[Any]], width: int, shape_id: int) -> str:
    rows, cols = len(grid), len(grid[0])
    col_w = width // cols
    row_h = min(370840, (_BOTTOM - _TOP) // rows)
    font = _font_size(rows)
    trs = "".join(
        f'<a:tr h="{row_h}">'
        + "".join(_cell_xml(_cell_text(v), _is_number(v), font) for v in row)
        + "</a:tr>"
        for row in grid
    )
    grid_cols = f'<a:gridCol w="{col_w}"/>' * cols
    return (
        f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{shape_id}" name="Table {shape_id}"/>'
        '<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/>'
        f'</p:nvGraphicFramePr><p:xfrm><a:off x="{_MARGIN}" y="{_TOP}"/>'
        f'<a:ext cx="{col_w * cols}" cy="{row_h * rows}"/></p:xfrm><a:graphic>'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl>'
        '<a:tblPr firstRow="1" bandRow="1">'
        f"<a:tableStyleId>{_TABLE_STYLE}</a:tableStyleId></a:tblPr>"
        f"<a:tblGrid>{grid_cols}</a:tblGrid>{trs}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    )


def _title_xml(title: str) -> str:
    return (
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/>'
        '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/>'
        "<p:txBody><a:bodyPr/><a:lstStyle/>"
        f'<a:p><a:r><a:rPr lang="ko-KR"/><a:t>{escape(title)}</a:t></a:r></a:p></p:txBody></p:sp>'
    )


def _slide_xml(title: str, table: str) -> str:
    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        f'xmlns:r="{_REL_NS}"><p:cSld><p:spTree><p:nvGrpSpPr>'
        '<p:cNvPr id="1" name=""/><p:cNvGrpSpPr/>'
        f"<p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>{_title_xml(title)}{table}</p:spTree></p:cSld>"
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )


def _text(parts: dict[str, bytes], name: str) -> str:
    if name not in parts:
        raise DropInError("document", f"pptx에 {name}가 없다")
    return parts[name].decode("utf-8")


def _title_layout(parts: dict[str, bytes]) -> str:
    """제목만 있는 슬라이드 레이아웃(없으면 첫 레이아웃)의 파일 이름."""
    layouts = sorted(
        (n for n in parts if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n)),
        key=lambda n: int(re.findall(r"\d+", n)[-1]),
    )
    if not layouts:
        raise DropInError("document", "pptx에 슬라이드 레이아웃이 없다")
    for name in layouts:
        if b'type="titleOnly"' in parts[name]:
            return name.rsplit("/", 1)[-1]
    return layouts[0].rsplit("/", 1)[-1]


def _slide_size(presentation: str) -> int:
    m = re.search(r'<p:sldSz[^>]*\bcx="(\d+)"', presentation)
    return int(m.group(1)) if m else 9144000


def _add_slide(package: Package, title: str, grid: list[list[Any]]) -> tuple[bytes, int, int]:
    """표 슬라이드를 맨 끝에 붙인 pptx 바이트와 (슬라이드 파일 번호, 표 도형 id)를 돌려준다."""
    parts = dict(package.parts)
    numbers = [int(m.group(1)) for n in parts if (m := _SLIDE_PART.fullmatch(n))]
    number = max(numbers, default=0) + 1
    presentation = _text(parts, "ppt/presentation.xml")
    pres_rels = _text(parts, "ppt/_rels/presentation.xml.rels")
    rid_no = max((int(i) for i in re.findall(r'Id="rId(\d+)"', pres_rels)), default=0) + 1
    sld_no = max(
        (int(i) for i in re.findall(r'<p:sldId [^>]*\bid="(\d+)"', presentation)), default=255
    )
    if "</p:sldIdLst>" not in presentation:
        raise DropInError("document", "pptx에 슬라이드 목록이 없다")
    part = f"ppt/slides/slide{number}.xml"
    width = _slide_size(presentation) - 2 * _MARGIN
    parts[part] = _slide_xml(title, _table_xml(grid, width, 3)).encode("utf-8")
    parts[f"ppt/slides/_rels/slide{number}.xml.rels"] = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{_REL_NS}/slideLayout" '
        f'Target="../slideLayouts/{_title_layout(parts)}"/></Relationships>'
    ).encode()
    new_id = f'<p:sldId id="{sld_no + 1}" r:id="rId{rid_no}"/>'
    parts["ppt/presentation.xml"] = presentation.replace(
        "</p:sldIdLst>", new_id + "</p:sldIdLst>", 1
    ).encode("utf-8")
    parts["ppt/_rels/presentation.xml.rels"] = _add_relationship(pres_rels, rid_no, number)
    types = _text(parts, "[Content_Types].xml")
    override = (
        f'<Override PartName="/{part}" ContentType="application/vnd.openxmlformats-officedocument.'
        'presentationml.slide+xml"/>'
    )
    parts["[Content_Types].xml"] = types.replace("</Types>", override + "</Types>", 1).encode(
        "utf-8"
    )
    stamp = package.infos[0].date_time if package.infos else (1980, 1, 1, 0, 0, 0)
    added = tuple(
        _new_info(name, stamp) for name in parts if name not in {i.filename for i in package.infos}
    )
    grown = Package((*package.infos, *added), parts)
    return grown.write(parts), number, 3


def _add_relationship(rels: str, rid_no: int, number: int) -> bytes:
    """관계 파일 끝에 슬라이드 관계를 더한다. 접두사(`rel:` 등)와 상관없이 닫는 태그를 찾는다."""
    m = re.search(r"</((?:[\w.-]+:)?)Relationships\s*>", rels)
    if m is None:
        raise DropInError("document", "presentation.xml.rels에 관계 목록이 없다")
    new = (
        f'<{m.group(1)}Relationship Id="rId{rid_no}" Type="{_REL_NS}/slide" '
        f'Target="slides/slide{number}.xml"/>'
    )
    return (rels[: m.start()] + new + rels[m.start() :]).encode("utf-8")


def _new_info(name: str, stamp: tuple[int, int, int, int, int, int]) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=stamp)
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _numbers(
    grid: list[list[Any]], first: tuple[int, int], source: Path, sheet: str, slide: int, shape: int
) -> list[dict[str, Any]]:
    """숫자 칸마다 문서 위치와 원본 출처(한 칸 `sum`)를 행 순서대로 만든다."""
    found: list[dict[str, Any]] = []
    for r, row in enumerate(grid):
        for c, value in enumerate(row):
            if not _is_number(value):
                continue
            addr = f"{column_letter(first[1] + c)}{first[0] + r}"
            digits = _digits(float(value))
            shown = float(f"{float(value):.{digits}f}")
            found.append(
                {
                    "value": int(shown) if shown == int(shown) else shown,
                    "kind": "table",
                    "location": {
                        "slide": slide,
                        "shape": str(shape),
                        "row": r + 1,
                        "column": c + 1,
                    },
                    "source": {
                        "file": str(source.resolve()),
                        "sheet": sheet,
                        "aggregation": "sum",
                        "range": f"{addr}:{addr}",
                        "literal": True,
                        "digits": digits,
                    },
                }
            )
    return found


def _existing(document: Path) -> list[dict[str, Any]]:
    path = provenance_path(document)
    if not path.is_file():
        return []
    try:
        numbers = json.loads(path.read_text(encoding="utf-8"))["numbers"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DropInError("provenance", f"기존 출처 파일을 읽지 못했다: {exc}") from exc
    if not isinstance(numbers, list):
        raise DropInError("provenance", f"출처 파일 형식이 맞지 않는다: {path.name}")
    return numbers


def _insert(document: Path, source: Path, cell_range: str, run_id: str) -> dict[str, Any]:
    if document.suffix.lower() != ".pptx" or not document.is_file():
        raise DropInError("input", f"지금은 pptx 문서에만 넣을 수 있다: {document.name}")
    if source.suffix.lower() != ".xlsx" or not source.is_file():
        raise DropInError("input", f"읽을 수 있는 .xlsx 파일이 아니다: {source}")
    r1, c1, r2, c2 = parse_range(cell_range)
    sheet = _first_sheet(source)
    version = source.stat().st_mtime_ns
    try:
        cells = read_cells(source, sheet)
    except RefreshError as exc:
        raise DropInError(exc.step, exc.message) from exc
    if source.stat().st_mtime_ns != version:
        raise DropInError(
            "source", f"읽는 동안 원본 엑셀이 바뀌었다. 다시 시도하세요: {source.name}"
        )
    grid = [
        [cells.get(f"{column_letter(c)}{r}") for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)
    ]
    original = document.read_bytes()
    try:
        data, slide, shape = _add_slide(Package.read(original), f"{source.name} 표", grid)
    except (OoxmlError, zipfile.BadZipFile, ValueError) as exc:
        raise DropInError("document", f"문서에 표를 넣지 못했다: {exc}") from exc
    added = _numbers(grid, (r1, c1), source, sheet, slide, shape)
    sidecar = dump([*_existing(document), *added])
    _commit(document, data, original, sidecar)
    return {
        "document": str(document),
        "provenance": str(provenance_path(document)),
        "slide": slide,
        "rows": len(grid),
        "columns": len(grid[0]),
        "numbers": len(added),
        "summary": (
            f"{source.name} {cell_range} 표를 새 슬라이드(파일 번호 {slide})에 넣고 "
            f"숫자 {len(added)}개의 출처를 기록했다"
        ),
        "run_id": run_id,
    }


def insert_range(document: str, source: str, cell_range: str) -> str:
    """엑셀 셀 범위를 pptx 문서 맨 끝 새 슬라이드에 표로 넣고 숫자마다 원본 출처를 기록한다.

    document는 넣을 pptx, source는 원본 xlsx(첫 시트), cell_range는 `A1:C16` 같은 범위다.
    숫자 칸의 출처는 문서 옆 `<문서 이름>.provenance.json`에 덧붙으며, 원본이 바뀌면
    `refresh_links`가 이 숫자도 고친다. 결과는 JSON 문자열이며, 실패하면 error와 실패한
    단계(step)를 담고 문서는 바뀌지 않는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        with _document_lock(Path(document)):
            result = _insert(Path(document), Path(source), cell_range, run_id)
    except DropInError as exc:
        result = {"error": exc.message, "step": exc.step, "run_id": run_id}
    except Exception as exc:  # file failures must not kill the caller
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected", "run_id": run_id}
    return json.dumps(result, ensure_ascii=False)
