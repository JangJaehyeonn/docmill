"""편집 범위(scope)를 문서 파트와 단위(도형·셀·문단)로 바꾼다 (FR-31).

- pptx: `{"slides": [2], "shapes": [2]}` — 슬라이드 번호(1부터)와 도형 id(cNvPr).
  도형을 빼면 슬라이드 전체.
- xlsx: `{"sheet": 1, "range": "C2:C4"}` — 시트 번호(1부터)와 셀 범위. 범위를 빼면 시트 전체.
- docx: `{"paragraphs": [3]}` — 본문 문단 번호(0부터).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from docmill.tools.ooxml import OoxmlError, Span, element_spans, ordered_targets
from docmill.tools.provenance import column_number

KINDS = (".pptx", ".xlsx", ".docx")
SHAPE_TAGS = frozenset({"sp", "grpSp", "graphicFrame", "cxnSp", "pic", "contentPart"})
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_CELL = re.compile(r"([A-Z]{1,3})([1-9][0-9]*)")
_SHAPE_ID = re.compile(r"<(?:[\w.-]+:)?cNvPr\b[^>]*?\sid=\"(\d+)\"")
_CELL_REF = re.compile(r"^<[^>]*?\sr=\"([A-Z]+[0-9]+)\"")
DOCUMENT_PART = "word/document.xml"


class ScopeError(Exception):
    """범위나 편집 대상을 해석하지 못했을 때 발생한다."""


@dataclass(frozen=True)
class GuardedPart:
    """단위로 나눠 지키는 파트 하나 (슬라이드·시트·본문)."""

    name: str
    label: str
    unit_kind: str
    want: Callable[[str, tuple[str, ...]], bool]
    key_of: Callable[[str, int], str]
    unit_format: str
    in_scope: Callable[[str], bool]
    whole: bool = False

    def unit_label(self, key: str) -> str:
        """단위 위치 문구 (예: `슬라이드 2 도형 3`)."""
        return self.unit_format.format(key)

    def units(self, text: str) -> tuple[list[Span], list[str]]:
        """파트 문자열의 단위 위치와 키를 문서 순서로 돌려준다. 키가 겹치면 `#n`을 붙인다."""
        spans = element_spans(text, self.want)
        keys: list[str] = []
        seen: dict[str, int] = {}
        for index, span in enumerate(spans):
            key = self.key_of(text[span.start : span.end], index)
            seen[key] = seen.get(key, 0) + 1
            keys.append(key if seen[key] == 1 else f"{key}#{seen[key]}")
        return spans, keys


def slide_parts(parts: dict[str, bytes]) -> list[str]:
    """발표 순서대로 슬라이드 파트 경로."""
    return _targets(parts, "ppt/presentation.xml", f"{_P}sldId")


def sheet_parts(parts: dict[str, bytes]) -> list[str]:
    """통합 문서 순서대로 시트 파트 경로."""
    return _targets(parts, "xl/workbook.xml", f"{_S}sheet")


def _targets(parts: dict[str, bytes], part: str, tag: str) -> list[str]:
    try:
        return ordered_targets(parts, part, tag)
    except (OoxmlError, KeyError, SyntaxError) as exc:
        raise ScopeError(f"문서 구조를 읽지 못했다: {exc}") from exc


def numbered(items: list[str], number: Any, what: str) -> str:
    """1부터 세는 번호로 목록 항목을 고른다."""
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= len(items):
        raise ScopeError(f"{what} 번호가 올바르지 않다: {number} (1~{len(items)})")
    return items[number - 1]


def int_list(raw: Any, what: str) -> list[int]:
    """정수 목록 인자를 검사한다."""
    if not isinstance(raw, list) or not raw:
        raise ScopeError(f"{what}은(는) 비어 있지 않은 번호 목록이어야 한다")
    if any(isinstance(x, bool) or not isinstance(x, int) for x in raw):
        raise ScopeError(f"{what}에는 정수만 넣을 수 있다: {raw}")
    return list(raw)


def parse_cell(ref: Any) -> tuple[int, int]:
    """`C10` → (열 번호, 행 번호)."""
    m = _CELL.fullmatch(ref) if isinstance(ref, str) else None
    if m is None:
        raise ScopeError(f"셀 주소가 올바르지 않다: {ref}")
    return column_number(m.group(1)), int(m.group(2))


def _check_keys(scope: dict[str, Any], allowed: set[str]) -> None:
    extra = sorted(set(scope) - allowed)
    if extra:
        raise ScopeError(f"알 수 없는 범위 항목: {', '.join(extra)}")


def shape_key(fragment: str, index: int) -> str:
    m = _SHAPE_ID.search(fragment)
    return m.group(1) if m else f"?{index}"


def _cell_key(fragment: str, index: int) -> str:
    m = _CELL_REF.match(fragment)
    return m.group(1) if m else f"?{index}"


def _pptx(parts: dict[str, bytes], scope: dict[str, Any]) -> list[GuardedPart]:
    _check_keys(scope, {"slides", "shapes"})
    slides = slide_parts(parts)
    chosen = set(int_list(scope.get("slides"), "slides"))
    for number in sorted(chosen):
        numbered(slides, number, "슬라이드")
    raw_shapes = scope.get("shapes")
    shapes = None if raw_shapes is None else _strs(set(int_list(raw_shapes, "shapes")))
    guarded = []
    for number, name in enumerate(slides, start=1):
        picked = number in chosen
        guarded.append(
            GuardedPart(
                name=name,
                label=f"슬라이드 {number}",
                unit_kind="도형",
                want=lambda n, p: n in SHAPE_TAGS and p[-1:] == ("spTree",),
                key_of=shape_key,
                unit_format=f"슬라이드 {number} 도형 {{}}",
                in_scope=shapes.__contains__ if picked and shapes is not None else _never,
                whole=picked and shapes is None,
            )
        )
    return guarded


def _never(_key: str) -> bool:
    return False


def _strs(numbers: set[int]) -> set[str]:
    return {str(n) for n in numbers}


def _range(raw: Any) -> tuple[tuple[int, int], tuple[int, int]] | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ScopeError(f"range는 'C2:C4' 같은 문자열이어야 한다: {raw}")
    first, _, last = raw.upper().partition(":")
    (c1, r1), (c2, r2) = parse_cell(first), parse_cell(last or first)
    return (min(c1, c2), min(r1, r2)), (max(c1, c2), max(r1, r2))


def _xlsx(parts: dict[str, bytes], scope: dict[str, Any]) -> list[GuardedPart]:
    _check_keys(scope, {"sheet", "range"})
    sheets = sheet_parts(parts)
    target = numbered(sheets, scope.get("sheet", 1), "시트")
    bounds = _range(scope.get("range"))

    def inside(key: str) -> bool:
        if bounds is None or not _CELL.fullmatch(key):
            return False
        col, row = parse_cell(key)
        (c1, r1), (c2, r2) = bounds
        return c1 <= col <= c2 and r1 <= row <= r2

    guarded = []
    for number, name in enumerate(sheets, start=1):
        picked = name == target
        guarded.append(
            GuardedPart(
                name=name,
                label=f"시트 {number}",
                unit_kind="셀",
                want=lambda n, p: n == "c" and p[-1:] == ("row",),
                key_of=_cell_key,
                unit_format=f"시트 {number} 셀 {{}}",
                in_scope=inside if picked else _never,
                whole=picked and bounds is None,
            )
        )
    return guarded


def _docx(parts: dict[str, bytes], scope: dict[str, Any]) -> list[GuardedPart]:
    _check_keys(scope, {"paragraphs"})
    if DOCUMENT_PART not in parts:
        raise ScopeError(f"{DOCUMENT_PART}가 없다")
    chosen = _strs(set(int_list(scope.get("paragraphs"), "paragraphs")))
    return [
        GuardedPart(
            name=DOCUMENT_PART,
            label="본문",
            unit_kind="문단",
            want=lambda n, p: n == "p" and p[-1:] == ("body",),
            key_of=lambda _fragment, index: str(index),
            unit_format="문단 {}",
            in_scope=chosen.__contains__,
        )
    ]


def layout(kind: str, parts: dict[str, bytes], scope: Any) -> list[GuardedPart]:
    """문서 종류와 범위로 지킬 파트 목록을 만든다."""
    if not isinstance(scope, dict):
        raise ScopeError("scope는 객체여야 한다")
    builders = {".pptx": _pptx, ".xlsx": _xlsx, ".docx": _docx}
    if kind not in builders:
        raise ScopeError(f"지원하지 않는 문서 종류: {kind}")
    return builders[kind](parts, scope)
