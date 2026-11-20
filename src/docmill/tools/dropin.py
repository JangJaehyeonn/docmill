"""`insert_range` 도구 (FR-44): 엑셀 셀 범위를 pptx 문서에 표로 넣는다.

studio에서 엑셀 탭의 범위를 PPT 탭으로 끌어다 놓으면 이 도구가 불린다. 범위의 셀을 읽어 새 슬라이드
(문서 맨 끝)에 표를 만들고, 숫자 셀마다 원본 출처(파일·시트·셀)를 문서 옆
`<문서 이름>.provenance.json`(FR-21)에 덧붙인다. 출처는 `sum` 집계의 한 칸 범위(`C2:C2`)라서
원본이 바뀌면 `refresh_links`(FR-37)가 이 표의 숫자도 고친다.

문서는 ADR 0024와 같이 패키지를 바이트 단위로 이어 붙여 고친다. 같은 입력이면 같은 결과다
(시각·난수 없음, 슬라이드·도형 번호는 문서에서 정해진다).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import re
import shutil
import signal
import sys
import tempfile
import unicodedata
import zipfile
from collections.abc import Iterator
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
    _publish,
    cells_from_bytes,
    number_digits,
    source_digest,
)

logger = logging.getLogger(__name__)

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
_CELL_SIDE = 91440  # 표 칸 왼쪽·오른쪽 기본 여백
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


def _pick_sheet(data: bytes, xlsx: Path, wanted: str | None) -> str:
    """고른 시트 이름을 확인해 돌려준다. 고르지 않았으면(None·빈 값) 첫 시트를 쓴다."""
    try:
        parts = Package.read(data).parts
        names = [
            str(s.get("name"))
            for s in ET.fromstring(parts["xl/workbook.xml"]).iter(f"{_S}sheet")
            if s.get("name")
        ]
    except (KeyError, ValueError, OoxmlError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise DropInError("source", f"원본 엑셀을 읽지 못했다: {xlsx} ({exc})") from exc
    if not names:
        raise DropInError("source", f"원본 엑셀에 시트가 없다: {xlsx}")
    if not wanted:
        return names[0]
    if wanted not in names:
        raise DropInError("source", f"원본 엑셀에 '{wanted}' 시트가 없다: {xlsx.name}")
    return wanted


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


def _text_width(text: str, font: int) -> float:
    """글자 너비 추정(EMU). 한글·전각은 글꼴 크기, 그 밖은 0.6배로 본다."""
    em = font / 100 * 12700
    return sum(em if unicodedata.east_asian_width(c) in "WF" else 0.6 * em for c in text)


def _row_heights(grid: list[list[Any]], col_w: int, font: int) -> list[int]:
    """칸 글자가 줄바꿈되는 줄 수까지 셈한 행 높이(EMU) 목록."""
    usable = max(1, col_w - 2 * _CELL_SIDE)
    line = font / 100 * 12700 * 1.2
    heights = []
    for row in grid:
        lines = max(
            sum(max(1, math.ceil(_text_width(part, font) / usable)) for part in text.split("\n"))
            for text in (_cell_text(v) for v in row)
        )
        heights.append(math.ceil(lines * line + 2 * _CELL_MARGIN))
    return heights


def _layout(grid: list[list[Any]], col_w: int) -> tuple[int, list[int]]:
    """표가 표 영역에 들어가는 가장 큰 글꼴과 행 높이. 가장 작은 글꼴로도 넘치면 거부한다."""
    font = _font_size(len(grid))
    while True:
        heights = _row_heights(grid, col_w, font)
        if sum(heights) <= _BOTTOM - _TOP:
            return font, heights
        if font <= _MIN_FONT:
            raise DropInError(
                "range",
                "긴 글자 때문에 표가 슬라이드에 넘친다. 범위를 줄이거나 나눠서 넣으세요",
            )
        font -= 100


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
    cols = len(grid[0])
    col_w = width // cols
    font, heights = _layout(grid, col_w)
    trs = "".join(
        f'<a:tr h="{h}">'
        + "".join(_cell_xml(_cell_text(v), _is_number(v), font) for v in row)
        + "</a:tr>"
        for row, h in zip(grid, heights, strict=True)
    )
    grid_cols = f'<a:gridCol w="{col_w}"/>' * cols
    return (
        f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{shape_id}" name="Table {shape_id}"/>'
        '<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/>'
        f'</p:nvGraphicFramePr><p:xfrm><a:off x="{_MARGIN}" y="{_TOP}"/>'
        f'<a:ext cx="{col_w * cols}" cy="{sum(heights)}"/></p:xfrm><a:graphic>'
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
    parts["[Content_Types].xml"] = _add_override(types, part)
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


def _add_override(types: str, part: str) -> bytes:
    """콘텐츠 타입 파일에 슬라이드 항목을 더한다. 닫는 태그는 접두사(`ct:` 등)와 상관없이 찾는다."""
    m = re.search(r"</((?:[\w.-]+:)?)Types\s*>", types)
    if m is None:
        raise DropInError("document", "[Content_Types].xml에 형식 목록이 없다")
    new = (
        f'<{m.group(1)}Override PartName="/{part}" ContentType="application/'
        'vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
    )
    return (types[: m.start()] + new + types[m.start() :]).encode("utf-8")


def _new_info(name: str, stamp: tuple[int, int, int, int, int, int]) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=stamp)
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _numbers(
    grid: list[list[Any]],
    first: tuple[int, int],
    origin: tuple[Path, str, str],
    slide: int,
    shape: int,
) -> list[dict[str, Any]]:
    """숫자 칸마다 문서 위치와 원본 출처(한 칸 `sum`)를 행 순서대로 만든다."""
    source, sheet, version = origin
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
                        "read_version": version,
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


def _journal(document: Path) -> Path:
    """발행 도중 끊겼을 때 되돌릴 이전 파일을 두는 폴더 (임시 폴더, 문서 경로별 하나)."""
    digest = hashlib.sha256(str(document.resolve()).encode("utf-8")).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / f"docmill-insert-{digest}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _recover(document: Path) -> str:
    """지난 삽입이 문서만 바꾸고 끊겼으면(문서는 새것, 출처 파일은 옛것) 두 파일을
    이전으로 되돌린다.

    두 파일이 모두 새것(발행은 끝나고 정리 전에 끊김)이거나 그 뒤 문서가 다시 바뀌었으면
    손대지 않는다.
    """
    journal = _journal(document)
    if not journal.is_dir():
        return "none"
    try:
        marks = json.loads((journal / "ready.json").read_text(encoding="utf-8"))
        doc_hash, prov_hash = str(marks["document"]), str(marks["provenance"])
    except (OSError, ValueError, KeyError, TypeError):
        shutil.rmtree(journal, ignore_errors=True)  # journal incomplete: nothing was published
        return "none"
    prov = provenance_path(document)
    doc_new = document.is_file() and _sha(document.read_bytes()) == doc_hash
    prov_new = prov.is_file() and _sha(prov.read_bytes()) == prov_hash
    rolled_back = doc_new and not prov_new
    if rolled_back:
        backup = journal / "provenance.bak"
        if backup.is_file():
            _publish(backup.read_bytes(), prov)
        else:
            prov.unlink(missing_ok=True)
        _publish((journal / "document.bak").read_bytes(), document)
    shutil.rmtree(journal, ignore_errors=True)
    if rolled_back:
        return "rolled_back"
    return "complete" if doc_new and prov_new else "none"


def recover_insert(document: str) -> str:
    """끊긴 삽입(문서만 바뀌고 출처 파일은 옛것)이 있으면 같은 잠금 안에서 두 파일을 되돌린다.

    studio가 `docmill insert-range`의 응답을 못 받았을 때(시간 초과 등) 곧바로 불러,
    출처 없는 표가 남은 문서를 사용자가 편집하기 전에 정리한다. 결과는 JSON 문자열
    (`recovered`: 되돌렸는지, `state`: `none`·`rolled_back`·`complete`)이다.
    `complete`는 지난 삽입이 두 파일을 모두 발행한 뒤 정리 전에 끊긴 경우다.
    """
    try:
        with _document_lock(Path(document)):
            state = _recover(Path(document))
    except Exception as exc:  # recovery failures must not kill the caller
        return json.dumps({"error": f"{type(exc).__name__}: {exc}", "step": "recover"})
    return json.dumps({"recovered": state == "rolled_back", "state": state})


@contextlib.contextmanager
def _signals_deferred() -> Iterator[None]:
    """두 파일을 발행하는 동안 종료 신호(시간 초과의 SIGTERM 등)를 미뤄 반쯤 발행되지 않게 한다."""
    if sys.platform == "win32":
        yield  # Windows has no signal mask; the journal recovers the next time instead
        return
    blocked = {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def _publish_pair(
    document: Path, data: bytes, original: bytes, sidecar: bytes, receipt: Path | None = None
) -> None:
    """pptx와 출처 파일을 함께 발행한다. 중간에 끊기면 다음 삽입이 저널로 둘 다 되돌린다."""
    journal = _journal(document)
    shutil.rmtree(journal, ignore_errors=True)
    journal.mkdir(parents=True)
    (journal / "document.bak").write_bytes(original)
    prov = provenance_path(document)
    if prov.is_file():
        (journal / "provenance.bak").write_bytes(prov.read_bytes())
    marks = {"document": _sha(data), "provenance": _sha(sidecar)}
    (journal / "ready.json").write_text(json.dumps(marks), encoding="utf-8")
    with _signals_deferred():
        _commit(document, data, original, sidecar)
        if receipt is not None and not _write_receipt(receipt):
            return  # keep the journal: recovery then reports "complete" to a caller that lost us
        shutil.rmtree(journal, ignore_errors=True)


def _write_receipt(receipt: Path) -> bool:
    """두 파일을 다 발행했다는 표시를 쓴다. 실패해도 삽입은 이미 끝났으므로 오류로 올리지 않는다."""
    try:
        receipt.write_text(json.dumps({"published": True}), encoding="utf-8")
    except OSError as exc:
        logger.warning("완료 표시를 쓰지 못했다 (삽입은 끝났다): %s", exc)
        return False
    return True


def _same_file(a: Path, b: Path) -> bool:
    if a.resolve() == b.resolve():
        return True
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def _check_receipt(receipt: Path, document: Path, source: Path) -> None:
    """완료 표시 파일이 문서·원본·출처 파일과 겹치면 아무것도 바꾸기 전에 거부한다."""
    for target in (document, source, provenance_path(document)):
        if _same_file(receipt, target):
            raise DropInError("input", f"완료 표시 파일이 작업 대상 파일과 겹친다: {receipt.name}")


def _insert(
    document: Path,
    source: Path,
    cell_range: str,
    sheet_name: str | None,
    run_id: str,
    receipt: Path | None = None,
) -> dict[str, Any]:
    if document.suffix.lower() != ".pptx" or not document.is_file():
        raise DropInError("input", f"지금은 pptx 문서에만 넣을 수 있다: {document.name}")
    if source.suffix.lower() != ".xlsx" or not source.is_file():
        raise DropInError("input", f"읽을 수 있는 .xlsx 파일이 아니다: {source}")
    r1, c1, r2, c2 = parse_range(cell_range)
    if receipt is not None:
        _check_receipt(receipt, document, source)
    _recover(document)
    raw = source.read_bytes()  # read once: values, sheet and version all come from these bytes
    sheet = _pick_sheet(raw, source, sheet_name)
    try:
        cells = cells_from_bytes(raw, sheet, source)
    except RefreshError as exc:
        raise DropInError(exc.step, exc.message) from exc
    grid = [
        [cells.get(f"{column_letter(c)}{r}") for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)
    ]
    original = document.read_bytes()
    try:
        data, slide, shape = _add_slide(Package.read(original), f"{source.name} 표", grid)
    except (OoxmlError, zipfile.BadZipFile, ValueError) as exc:
        raise DropInError("document", f"문서에 표를 넣지 못했다: {exc}") from exc
    added = _numbers(grid, (r1, c1), (source, sheet, source_digest(raw)), slide, shape)
    sidecar = dump([*_existing(document), *added])
    _publish_pair(document, data, original, sidecar, receipt)
    return {
        "document": str(document),
        "provenance": str(provenance_path(document)),
        "slide": slide,
        "rows": len(grid),
        "columns": len(grid[0]),
        "numbers": len(added),
        "sheet": sheet,
        "summary": (
            f"{source.name} '{sheet}' {cell_range} 표를 새 슬라이드(파일 번호 {slide})에 넣고 "
            f"숫자 {len(added)}개의 출처를 기록했다"
        ),
        "run_id": run_id,
    }


def insert_range(document: str, source: str, cell_range: str, sheet: str | None = None) -> str:
    """엑셀 셀 범위를 pptx 문서 맨 끝 새 슬라이드에 표로 넣고 숫자마다 원본 출처를 기록한다.

    document는 넣을 pptx, source는 원본 xlsx, cell_range는 `A1:C16` 같은 범위,
    sheet는 범위를 읽을 시트 이름이다(없으면 첫 시트). 출처에는 읽은 원본의 판
    (`read_version`)도 적는다.
    숫자 칸의 출처는 문서 옆 `<문서 이름>.provenance.json`에 덧붙으며, 원본이 바뀌면
    `refresh_links`가 이 숫자도 고친다. 결과는 JSON 문자열이며, 실패하면 error와 실패한
    단계(step)를 담고 문서는 바뀌지 않는다.
    """
    return insert_range_with_receipt(document, source, cell_range, sheet, None)


def insert_range_with_receipt(
    document: str, source: str, cell_range: str, sheet: str | None, receipt: str | None
) -> str:
    """`insert_range`와 같지만, receipt가 있으면 두 파일 발행을 마친 순간 완료 표시를 쓴다.

    studio가 `docmill insert-range --receipt`로 불러, 응답을 잃었을 때(시간 초과 등) 이 호출이
    실제로 표를 넣었는지 판단한다. 잠금을 기다리다 끝나면 표시가 없다.
    """
    run_id = current_run_id() or new_run_id()
    mark = Path(receipt) if receipt else None
    try:
        with _document_lock(Path(document)):
            result = _insert(Path(document), Path(source), cell_range, sheet, run_id, mark)
    except DropInError as exc:
        result = {"error": exc.message, "step": exc.step, "run_id": run_id}
    except Exception as exc:  # file failures must not kill the caller
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected", "run_id": run_id}
    return json.dumps(result, ensure_ascii=False)
