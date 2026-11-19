"""`compare_versions` 업무 도구 (FR-41).

같은 문서의 두 버전에서 바뀐 숫자·문장·표 셀을 위치와 함께 돌려준다. 규칙 기반이며
(AI·엔진 호출 없음, hwp 글자 읽기 제외) 같은 입력이면 같은 목록을 문서 순서대로 낸다.

- `숫자`: 문장의 나머지는 같고 숫자만 다름 (숫자 하나당 한 건). 표 셀 값이 숫자여도 여기에 센다.
- `문장`: 본문 문단의 글자가 바뀜 (문단이 새로 생기거나 사라진 경우 old·new 중 하나는 빈 글자)
- `표 셀`: 표·시트 셀의 글자가 바뀜 (숫자만 바뀐 셀은 `숫자`)

지원 형식은 docx·pptx·xlsx·hwp·hwpx이며 두 파일은 같은 형식이어야 한다.
"""

from __future__ import annotations

import json
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from docmill.runlog import current_run_id, new_run_id
from docmill.tools.contradictions import read_content
from docmill.tools.hwp import HwpError, is_hwp, read_hwp_paragraphs, read_hwp_tables
from docmill.tools.review import ReviewError

NUMBER = "숫자"
SENTENCE = "문장"
CELL = "표 셀"

_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
# A leading "-" is a sign only when it does not follow a word character ("2026-11-20" is a date).
_NUM = re.compile(r"(?:(?<![\w])-)?\d[\d,]*(?:\.\d+)?")
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")
_XLSX = (".xlsx",)
_OOXML = (".docx", ".pptx")


@dataclass
class Snapshot:
    """한 버전에서 읽은 본문 문단 목록과 위치별 셀 값. 둘 다 문서 순서다."""

    prose: list[tuple[str, str]] = field(default_factory=list)
    cells: dict[str, str] = field(default_factory=dict)


# ── 읽기 ────────────────────────────────────────────────────────────────


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref)
    index = 0
    for ch in letters.group(0) if letters else "":
        index = index * 26 + ord(ch) - 64
    return index


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(f"{_S}t")) for si in root.iter(f"{_S}si")]


def _sheet_parts(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """(시트 이름, 파트 경로)를 통합 문서 순서로 돌려준다."""
    rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id", ""): r.get("Target", "") for r in rels_root.iter(f"{_REL}Relationship")}
    book = ET.fromstring(archive.read("xl/workbook.xml"))
    out: list[tuple[str, str]] = []
    for sheet in book.iter(f"{_S}sheet"):
        target = targets.get(sheet.get(f"{_R}id", ""), "")
        joined = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        part = posixpath.normpath(joined)
        out.append((sheet.get("name", ""), part))
    return out


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{_S}t"))
    value = cell.find(f"{_S}v")
    text = (value.text or "") if value is not None else ""
    if kind == "s" and text.isdigit() and int(text) < len(shared):
        return shared[int(text)]
    return text


def _xlsx_snapshot(archive: zipfile.ZipFile) -> Snapshot:
    snap = Snapshot()
    shared = _shared_strings(archive)
    for name, part in _sheet_parts(archive):
        root = ET.fromstring(archive.read(part))
        cells = [(c.get("r", ""), c) for c in root.iter(f"{_S}c")]
        for ref, cell in sorted(cells, key=lambda x: _row_col(x[0])):
            value = _cell_value(cell, shared)
            if value != "":
                snap.cells[f"{name} {ref}"] = value
    return snap


def _row_col(ref: str) -> tuple[int, int]:
    digits = re.search(r"\d+", ref)
    return (int(digits.group(0)) if digits else 0, _col_index(ref))


def _ooxml_snapshot(path: Path) -> Snapshot:
    content = read_content(path)
    snap = Snapshot(prose=[(p.place, p.text) for p in content.prose])
    for table in content.tables:
        for r, row in enumerate(table.rows):
            for c, text in enumerate(row):
                if text:
                    snap.cells[f"{table.place} {r + 1}행 {c + 1}열"] = text
    return snap


def _hwp_snapshot(path: Path) -> Snapshot:
    """hwp 본문 문단과 표 셀(표·행·열 위치)을 읽는다."""
    snap = Snapshot()
    for para in read_hwp_paragraphs(path):
        place = f"구역 {para['section']} 문단 {para['paragraph']}"
        snap.prose.append((place, str(para["text"])))
    for cell in read_hwp_tables(path):
        where = f"구역 {cell['section']} 문단 {cell['paragraph']} 표 {cell['table']}"
        snap.cells[f"{where} {cell['row']}행 {cell['col']}열"] = str(cell["text"])
    return snap


def read_snapshot(path: Path) -> Snapshot:
    """문서에서 본문 문단과 셀 값을 읽는다. 읽지 못하면 ReviewError."""
    suffix = path.suffix.lower()
    if not path.is_file():
        raise ReviewError("open", f"파일을 찾을 수 없습니다: {path}")
    if is_hwp(path):
        try:
            return _hwp_snapshot(path)
        except HwpError as exc:
            raise ReviewError("read", f"문서를 읽지 못했습니다: {exc}") from exc
    if suffix not in (*_XLSX, *_OOXML):
        raise ReviewError("open", f"지원하지 않는 형식입니다(docx·pptx·xlsx·hwp): {path}")
    if suffix in _OOXML:
        return _ooxml_snapshot(path)
    try:
        with zipfile.ZipFile(path) as archive:
            return _xlsx_snapshot(archive)
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError) as exc:
        raise ReviewError("read", f"문서를 읽지 못했습니다: {exc}") from exc


# ── 비교 ────────────────────────────────────────────────────────────────


def _change(kind: str, location: str, old: str, new: str) -> dict[str, str]:
    return {"kind": kind, "location": location, "old": old, "new": new}


def _same_place(old: list[str], new: list[str], key: Any) -> list[tuple[int | None, int | None]]:
    """글자(또는 key 변환값)가 같은 항목을 먼저 맞추고, 남은 같은 개수 구간만 순서대로 짝짓는다."""
    pairs: list[tuple[int | None, int | None]] = []
    matcher = SequenceMatcher(a=[key(t) for t in old], b=[key(t) for t in new], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            pairs += [(i1 + k, j1 + k) for k in range(i2 - i1)]
        else:
            pairs += [(i, None) for i in range(i1, i2)] + [(None, j) for j in range(j1, j2)]
    return pairs


def _align(old: list[str], new: list[str]) -> list[tuple[int | None, int | None]]:
    """같은 글자 → 숫자만 다른 글자 순으로 짝지어 (이전 번호, 새 번호) 목록을 만든다.

    짝이 없는 쪽은 None이다 (삭제·삽입). 문서 순서(새 버전 기준)를 지킨다.
    """
    pairs: list[tuple[int | None, int | None]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        inner = _same_place(old[i1:i2], new[j1:j2], _skeleton)
        pairs += [(None if a is None else i1 + a, None if b is None else j1 + b) for a, b in inner]
    return pairs


def _skeleton(text: str) -> str:
    return _NUM.sub("\0", text)


def _number_changes(place: str, old: str, new: str) -> list[dict[str, str]]:
    pairs = zip(_NUM.findall(old), _NUM.findall(new), strict=True)
    return [_change(NUMBER, place, a, b) for a, b in pairs if a != b]


def _diff_text(place: str, old: str, new: str, text_kind: str) -> list[dict[str, str]]:
    """같은 자리의 글자 두 개를 비교한다. 숫자만 다르면 숫자 하나당 한 건이다."""
    if old == new:
        return []
    if _skeleton(old) == _skeleton(new):
        return _number_changes(place, old, new)
    return [_change(text_kind, place, old, new)]


def _diff_paragraph(place: str, old: str, new: str) -> list[dict[str, str]]:
    """문단 하나를 비교한다. 문장이 여럿이면 문장끼리 짝지어 숫자·문장 변경을 따로 센다."""
    if old == new:
        return []
    if _skeleton(old) == _skeleton(new):
        return _number_changes(place, old, new)
    a, b = _SENTENCE_END.split(old.strip()), _SENTENCE_END.split(new.strip())
    if len(a) < 2 and len(b) < 2:
        return [_change(SENTENCE, place, old, new)]
    found: list[dict[str, str]] = []
    for i, j in _align(a, b):
        found += _diff_text(
            place, a[i] if i is not None else "", b[j] if j is not None else "", SENTENCE
        )
    return found


def _prose_changes(before: list[tuple[str, str]], after: list[tuple[str, str]]) -> list[Any]:
    """문단 목록을 짝지어 바뀐 문단을 모은다. 위치는 새 버전 기준(없으면 이전)이다."""
    old_texts, new_texts = [t for _, t in before], [t for _, t in after]
    found: list[dict[str, str]] = []
    for i, j in _align(old_texts, new_texts):
        place = (after[j] if j is not None else before[i or 0])[0]
        found += _diff_paragraph(
            place, old_texts[i] if i is not None else "", new_texts[j] if j is not None else ""
        )
    return found


def _cell_changes(before: dict[str, str], after: dict[str, str]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for place in [*before, *(p for p in after if p not in before)]:
        old, new = before.get(place, ""), after.get(place, "")
        found += _diff_text(place, old, new, CELL)
    return found


def build_comparison(before: Path, after: Path) -> dict[str, Any]:
    """두 버전에서 바뀐 곳 목록(본문 변경, 셀 변경 순)과 한 줄 요약을 만든다."""
    if before.suffix.lower() != after.suffix.lower():
        raise ReviewError("open", "두 파일의 형식이 같아야 합니다")
    old, new = read_snapshot(before), read_snapshot(after)
    changes = _prose_changes(old.prose, new.prose) + _cell_changes(old.cells, new.cells)
    counts = {k: sum(1 for c in changes if c["kind"] == k) for k in (NUMBER, SENTENCE, CELL)}
    detail = ", ".join(f"{k} {n}곳" for k, n in counts.items() if n)
    summary = f"바뀐 곳 {len(changes)}곳 ({detail})" if changes else "바뀐 곳 없음"
    return {"changes": changes, "summary": summary}


def compare_versions(before: str, after: str) -> str:
    """같은 문서의 두 버전에서 바뀐 숫자·문장·표 셀을 위치와 함께 돌려준다 (규칙 기반, AI 없음).

    before(이전 버전)와 after(새 버전)는 같은 형식(docx·pptx·xlsx·hwp·hwpx)의 파일 경로다.
    결과 JSON의 changes에는 종류(kind: '숫자'·'문장'·'표 셀'), 위치(location), 이전 값(old),
    새 값(new)이 문서 순서로 담긴다. 같은 문서면 빈 목록이다. 실패하면 error와 실패한
    단계(step)를 담는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        result = build_comparison(Path(before), Path(after))
    except ReviewError as exc:
        result = {"error": exc.message, "step": exc.step, "changes": []}
    except Exception as exc:  # document failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected", "changes": []}
    result["run_id"] = run_id
    return json.dumps(result, ensure_ascii=False)
