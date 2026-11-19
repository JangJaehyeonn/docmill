"""`find_contradictions` 업무 도구 (FR-40).

원본 없이 문서 안에서 숫자끼리 맞지 않는 곳을 찾는다. 세 가지를 본다.

- `합계 불일치`: 표의 항목 합과 합계 행·열의 값이 다름
- `비율 불일치`: 본문의 '30% 증가' 같은 비율·증감이 같은 문서 표에서 계산한 값과 다름
- `항목 불일치`: 같은 항목('영업1팀 매출은 1,200만원')의 숫자가 쪽·슬라이드마다 다름

규칙 기반이며(AI·엔진·시각·난수 없음) 같은 입력이면 같은 목록을 문서 순서대로 돌려준다.
지원 형식은 docx·pptx다.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from docmill.runlog import current_run_id, new_run_id
from docmill.tools.provenance import slide_parts
from docmill.tools.review import ReviewError

TOTAL_MISMATCH = "합계 불일치"
RATIO_MISMATCH = "비율 불일치"
ITEM_MISMATCH = "항목 불일치"

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_NUMBER = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_TOTAL_LABEL = re.compile(r"^(?:합\s*계|총\s*계|총\s*합|소\s*계|total|sum)$", re.IGNORECASE)
_CHANGE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*(p|포인트)?\s*(증가|상승|늘|확대|성장|감소|하락|줄|축소|떨어)"
)
_UNITS = "만원|억원|천원|원|%|명|건|개|대|곳"
_ITEM_VALUE = re.compile(
    r"\s*([가-힣A-Za-z0-9][가-힣A-Za-z0-9 \t]*?)\s*(?:은|는)\s*"
    r"([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(" + _UNITS + ")"
)
# a clause ends at sentence punctuation or at a connective right after a unit
_CLAUSE_SPLIT = re.compile(
    r"\.(?!\d)|,(?!\d{3})|[!?;\n。]+|(?<=[원명건개대곳%])(?:이고|이며|이었고|였고)"
)
_SUBJECT = re.compile(r"[가-힣A-Za-z0-9]+(?:은|는|이|가)(?=\s|$)")
_UNIT_AT_END = re.compile(r"\s*(" + _UNITS + ")$")
# unit -> (family, scale to the family's base unit); units of one family are convertible
_UNIT_SCALE = {"억원": ("원", 1e8), "만원": ("원", 1e4), "천원": ("원", 1e3), "원": ("원", 1.0)}
_DOWN = ("감소", "하락", "줄", "축소", "떨어")
_PCT_TOLERANCE = 0.5
_VALUE_TOLERANCE = 1e-6


@dataclass
class Table:
    """표 하나. rows는 셀 글자 목록, place는 위치 앞머리('슬라이드 2 표 1' 등)."""

    place: str
    rows: list[list[str]]


@dataclass
class Prose:
    """본문 문단 하나와 그 위치 문자열."""

    place: str
    text: str


@dataclass
class Content:
    """문서에서 읽은 표와 본문. 둘 다 문서 순서다."""

    tables: list[Table] = field(default_factory=list)
    prose: list[Prose] = field(default_factory=list)


# ── 읽기 ────────────────────────────────────────────────────────────────


def _text(node: ET.Element, ns: str) -> str:
    parts = [
        (e.text or "") if e.tag == f"{ns}t" else " " for e in node.iter() if e.tag in _TEXT_TAGS[ns]
    ]
    return "".join(parts).strip()


_TEXT_TAGS = {
    _A: {f"{_A}t", f"{_A}br", f"{_A}tab"},
    _W: {f"{_W}t", f"{_W}br", f"{_W}tab"},
}


def _docx_content(archive: zipfile.ZipFile) -> Content:
    root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find(f"{_W}body")
    content = Content()
    if body is None:
        return content
    page, p_no, t_no = 1, 0, 0
    for child in body:
        if child.tag == f"{_W}p":
            p_no += 1
            if child.find(f".//{_W}lastRenderedPageBreak") is not None:
                page += 1
            text = _text(child, _W)
            if text:
                content.prose.append(Prose(f"쪽 {page} 문단 {p_no}", text))
            page += sum(1 for b in child.iter(f"{_W}br") if b.get(f"{_W}type") == "page")
        elif child.tag == f"{_W}tbl":
            t_no += 1
            rows = [
                [_text(tc, _W) for tc in tr.findall(f"{_W}tc")] for tr in child.findall(f"{_W}tr")
            ]
            content.tables.append(Table(f"표 {t_no}", rows))
    return content


def _pptx_content(archive: zipfile.ZipFile) -> Content:
    content = Content()
    for slide, part in enumerate(slide_parts(archive), start=1):
        root = ET.fromstring(archive.read(part))
        p_no = t_no = 0
        for node in root.iter():
            if node.tag == f"{_P}sp":
                for para in node.iter(f"{_A}p"):
                    p_no += 1
                    text = _text(para, _A)
                    if text:
                        content.prose.append(Prose(f"슬라이드 {slide} 문단 {p_no}", text))
            elif node.tag == f"{_A}tbl":
                t_no += 1
                rows = [
                    [_text(tc, _A) for tc in tr.findall(f"{_A}tc")]
                    for tr in node.findall(f"{_A}tr")
                ]
                content.tables.append(Table(f"슬라이드 {slide} 표 {t_no}", rows))
    return content


def read_content(document: Path) -> Content:
    """docx·pptx에서 표와 본문 문단을 읽는다."""
    reader = {".docx": _docx_content, ".pptx": _pptx_content}.get(document.suffix.lower())
    if reader is None:
        raise ReviewError("open", f"지원하지 않는 형식입니다(pptx·docx만): {document}")
    if not document.is_file():
        raise ReviewError("open", f"파일을 찾을 수 없습니다: {document}")
    try:
        with zipfile.ZipFile(document) as archive:
            return reader(archive)
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError) as exc:
        raise ReviewError("read", f"문서를 읽지 못했습니다: {exc}") from exc


# ── 숫자 도우미 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Quantity:
    """숫자와 단위. base는 같은 계열(원 등)의 기본 단위로 환산한 값이다."""

    value: float
    unit: str

    @property
    def family(self) -> str:
        """환산 가능한 단위 계열. 단위가 없으면 빈 문자열."""
        return _UNIT_SCALE.get(self.unit, (self.unit, 1.0))[0]

    @property
    def scale(self) -> float:
        """이 단위 1이 기본 단위 몇인지."""
        return _UNIT_SCALE.get(self.unit, (self.unit, 1.0))[1]

    @property
    def base(self) -> float:
        """기본 단위로 환산한 값."""
        return self.value * self.scale


def _quantity(cell: str) -> Quantity | None:
    """셀 글자 전체가 숫자(쉼표·단위 허용)일 때만 값과 단위를 돌려준다."""
    text = cell.strip()
    unit_match = _UNIT_AT_END.search(text)
    unit = unit_match.group(1) if unit_match else ""
    stripped = text[: unit_match.start()] if unit_match else text
    if not _NUMBER.fullmatch(stripped):
        return None
    return Quantity(float(stripped.replace(",", "")), unit)


def _compatible(quantities: list[Quantity]) -> bool:
    """단위가 있는 값끼리 같은 계열일 때만 함께 계산할 수 있다."""
    return len({q.family for q in quantities if q.unit}) <= 1


def _fmt(value: float) -> str:
    rounded = round(value, 2)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= _VALUE_TOLERANCE * max(1.0, abs(b))


def _entry(place: str, text: str) -> dict[str, str]:
    return {"location": place, "text": text}


# ── (1) 합계 ────────────────────────────────────────────────────────────


def _cell_place(table: Table, row: int, col: int) -> str:
    return f"{table.place} {row + 1}행 {col + 1}열"


def _is_subtotal(label: str) -> bool:
    return label.strip().replace(" ", "").startswith("소계")


def _total_row_items(table: Table, total_row: int, col: int) -> list[Quantity]:
    """합계 행 위쪽의 항목 값. 소계 행은 직전 합계·소계 행 이후 구간만 합산한다."""
    start = 1
    if _is_subtotal(table.rows[total_row][0]):
        for r in range(total_row - 1, 0, -1):
            if table.rows[r] and _TOTAL_LABEL.match(table.rows[r][0].strip()):
                start = r + 1
                break
    values: list[Quantity] = []
    for r in range(start, total_row):
        row = table.rows[r]
        label = row[0] if row else ""
        if col < len(row) and not _TOTAL_LABEL.match(label.strip()):
            value = _quantity(row[col])
            if value is not None:
                values.append(value)
    return values


def _sum_mismatch(items: list[Quantity], actual: Quantity | None) -> float | None:
    """항목 합이 합계 칸과 다르면 합계 칸 단위로 환산한 항목 합을, 같으면 None을 돌려준다."""
    if actual is None or len(items) < 2 or not _compatible([*items, actual]):
        return None
    total = sum(q.base for q in items)
    if _close(total, actual.base):
        return None
    return total / actual.scale


def _row_checks(table: Table) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for r, row in enumerate(table.rows):
        if r == 0 or not row or not _TOTAL_LABEL.match(row[0].strip()):
            continue
        for c in range(1, len(row)):
            actual = _quantity(row[c])
            expected = _sum_mismatch(_total_row_items(table, r, c), actual)
            if actual is not None and expected is not None:
                found.append(_mismatch(table, r, c, expected, actual, "열"))
    return found


def _col_checks(table: Table) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    header = table.rows[0] if table.rows else []
    for c, title in enumerate(header):
        if c == 0 or not _TOTAL_LABEL.match(title.strip()):
            continue
        for r in range(1, len(table.rows)):
            row = table.rows[r]
            if c >= len(row) or _TOTAL_LABEL.match(row[0].strip()):
                continue
            actual = _quantity(row[c])
            items = [v for v in (_quantity(x) for x in row[1:c]) if v is not None]
            expected = _sum_mismatch(items, actual)
            if actual is not None and expected is not None:
                found.append(_mismatch(table, r, c, expected, actual, "행"))
    return found


def _mismatch(
    table: Table, row: int, col: int, expected: float, actual: Quantity, axis: str
) -> dict[str, Any]:
    place = _cell_place(table, row, col)
    cell = table.rows[row][col]
    exp_text = f"{_fmt(expected)}{actual.unit}"
    act_text = f"{_fmt(actual.value)}{actual.unit}"
    return {
        "kind": TOTAL_MISMATCH,
        "locations": [_entry(place, cell)],
        "expected": exp_text,
        "actual": act_text,
        "message": f"{place}의 합계 {act_text}이(가) 항목 {axis} 합 {exp_text}과 다름",
    }


def total_contradictions(tables: list[Table]) -> list[dict[str, Any]]:
    """합계 행·열이 항목 합과 다른 셀을 찾는다."""
    found: list[dict[str, Any]] = []
    for table in tables:
        found += _row_checks(table) + _col_checks(table)
    return found


# ── (2) 비율·증감 ───────────────────────────────────────────────────────


@dataclass
class Change:
    """표 한 행의 이웃한 두 숫자 칸 사이 변화. pp는 둘 다 %일 때의 %p 차이다."""

    place: str
    name: str
    pct: float
    pp: float | None


def _table_changes(tables: list[Table]) -> list[Change]:
    """표의 행마다 이웃한 두 숫자 칸의 증감률(%)과 %p 차이를 모은다."""
    out: list[Change] = []
    for table in tables:
        for r, row in enumerate(table.rows):
            if r == 0 or not row or _TOTAL_LABEL.match(row[0].strip()):
                continue
            nums = [(c, _quantity(x)) for c, x in enumerate(row) if c > 0]
            for (c1, a), (c2, b) in pairwise(nums):
                if a is None or b is None or c2 != c1 + 1 or not a.base or not _compatible([a, b]):
                    continue
                pp = b.value - a.value if a.unit == b.unit == "%" else None
                out.append(
                    Change(
                        _cell_place(table, r, c2), row[0], (b.base - a.base) / abs(a.base) * 100, pp
                    )
                )
    return out


def _claims(text: str) -> list[tuple[float, str, bool, int]]:
    """본문의 증감 주장을 (부호 있는 값, 원문, %p 여부, 시작 위치)로 모은다."""
    return [
        (
            float(m.group(1)) * (-1 if m.group(3) in _DOWN else 1),
            m.group(0),
            m.group(2) is not None,
            m.start(),
        )
        for m in _CHANGE.finditer(text)
    ]


def _ratio_for(
    para: Prose, claim: tuple[float, str, bool, int], changes: list[Change]
) -> dict[str, Any] | None:
    value, phrase, is_pp, start = claim
    related = [c for c in changes if c.name.strip() and c.name.strip() in para.text]
    if not related and _SUBJECT.search(para.text[:start]):
        return None  # 본문이 표에 없는 다른 항목을 말하고 있다
    pool = related or changes
    candidates: list[tuple[str, float]] = []
    for c in pool:
        v = c.pp if is_pp else c.pct
        if v is not None:
            candidates.append((c.place, v))
    if any(abs(v - value) <= _PCT_TOLERANCE for _, v in candidates):
        return None
    if len(candidates) != 1:
        return None  # 어느 표 값과 비교해야 하는지 정할 수 없으면 알리지 않는다
    place, expected = candidates[0]
    suffix = "%p" if is_pp else "%"
    return {
        "kind": RATIO_MISMATCH,
        "locations": [_entry(para.place, para.text), _entry(place, "")],
        "expected": f"{_fmt(abs(expected))}{suffix}",
        "actual": f"{_fmt(abs(value))}{suffix}",
        "message": f"본문 '{phrase}'이(가) 표에서 계산한 {_fmt(expected)}{suffix}와 다릅니다.",
    }


def ratio_contradictions(content: Content) -> list[dict[str, Any]]:
    """본문 비율·증감이 표에서 계산한 값과 다른 곳을 찾는다."""
    changes = _table_changes(content.tables)
    found: list[dict[str, Any]] = []
    if not changes:
        return found
    for para in content.prose:
        for claim in _claims(para.text):
            item = _ratio_for(para, claim, changes)
            if item:
                found.append(item)
    return found


# ── (3) 같은 항목 ───────────────────────────────────────────────────────


@dataclass
class Hit:
    """같은 항목 이름으로 모은 값 하나와 그 위치."""

    place: str
    text: str
    quantity: Quantity


def _prose_hits(prose: list[Prose]) -> list[tuple[str, Hit]]:
    hits: list[tuple[str, Hit]] = []
    for para in prose:
        for clause in _CLAUSE_SPLIT.split(para.text):
            m = _ITEM_VALUE.match(clause)
            if m:
                key = re.sub(r"\s+", "", m.group(1))
                quantity = Quantity(float(m.group(2).replace(",", "")), m.group(3))
                hits.append((key, Hit(para.place, para.text, quantity)))
    return hits


def _table_hits(tables: list[Table]) -> list[tuple[str, Hit]]:
    """표의 '항목 이름 | 값' 칸을 (항목 키, 값)으로 모은다. 키에는 열 제목이 붙는다."""
    hits: list[tuple[str, Hit]] = []
    for table in tables:
        header = table.rows[0] if table.rows else []
        for r, row in enumerate(table.rows):
            label = row[0].strip() if row else ""
            if r == 0 or not label or _TOTAL_LABEL.match(label):
                continue
            for c in range(1, len(row)):
                quantity = _quantity(row[c])
                title = header[c].strip() if c < len(header) else ""
                if quantity is not None and title:
                    key = re.sub(r"\s+", "", label) + (f"({title})" if len(row) > 2 else "")
                    hits.append((key, Hit(_cell_place(table, r, c), row[c], quantity)))
    return hits


def _differs(a: Quantity, b: Quantity) -> bool:
    if a.unit and b.unit and a.family != b.family:
        return True
    return not _close(a.base, b.base)


def item_contradictions(content: Content) -> list[dict[str, Any]]:
    """같은 항목이 쪽·슬라이드·표마다 다른 숫자로 나오는 곳을 찾는다."""
    seen: dict[str, list[Hit]] = {}
    for key, hit in _prose_hits(content.prose) + _table_hits(content.tables):
        seen.setdefault(key, []).append(hit)
    found: list[dict[str, Any]] = []
    for key, hits in seen.items():
        first = hits[0].quantity
        if not any(_differs(h.quantity, first) for h in hits[1:]):
            continue
        shown = [f"{_fmt(h.quantity.value)}{h.quantity.unit}" for h in hits]
        found.append(
            {
                "kind": ITEM_MISMATCH,
                "item": key,
                "locations": [_entry(h.place, h.text) for h in hits],
                "expected": shown[0],
                "actual": shown[-1],
                "message": f"'{key}'이(가) 위치마다 다릅니다: {', '.join(shown)}",
            }
        )
    return found


# ── 도구 ────────────────────────────────────────────────────────────────


def build_contradictions(document: Path) -> dict[str, Any]:
    """문서 안 숫자 모순 목록(종류별, 문서 순서)을 만든다."""
    content = read_content(document)
    items = (
        total_contradictions(content.tables)
        + ratio_contradictions(content)
        + item_contradictions(content)
    )
    summary = f"숫자 모순 {len(items)}건" if items else "숫자 모순 없음"
    return {"contradictions": items, "summary": summary}


def find_contradictions(document: str) -> str:
    """원본 없이 문서 안에서 숫자끼리 맞지 않는 곳을 찾는다 (규칙 기반, AI 없음).

    document는 docx·pptx 경로다. 결과 JSON의 contradictions에는 종류(kind: '합계 불일치'·
    '비율 불일치'·'항목 불일치'), 위치 목록(locations: 위치 문자열과 해당 글자), 계산한 값
    (expected), 문서에 적힌 값(actual)이 담긴다. 모순이 없으면 빈 목록이다.
    실패하면 error와 실패한 단계(step)를 담는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        result = build_contradictions(Path(document))
    except ReviewError as exc:
        result = {"error": exc.message, "step": exc.step, "contradictions": []}
    except Exception as exc:  # document failures must not kill the server
        result = {
            "error": f"{type(exc).__name__}: {exc}",
            "step": "unexpected",
            "contradictions": [],
        }
    result["run_id"] = run_id
    return json.dumps(result, ensure_ascii=False)
