"""`guarded_edit`의 기본 편집 함수 (FR-31).

편집 내용(edit)을 문서 파트 문자열에 바로 적용한다. 바꾸는 요소만 새로 쓰고 나머지 바이트는
그대로 둔다. 범위 검사는 하지 않는다 — 범위 밖까지 바꾼 편집도 그대로 적용하고,
되돌리기는 `guard` 모듈이 맡는다.

- pptx: `{"set_text": [{"slide": 2, "shape": 2, "text": "새 제목"}]}` (줄바꿈은 문단으로 나눈다)
- xlsx: `{"set_cells": {"C2": 999}}`, 선택 `"sheet"`(기본은 범위의 시트)
- docx: `{"set_paragraphs": {"3": "새 문장"}}`
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from xml.sax.saxutils import escape

from docmill.tools.ooxml import OoxmlError, Span, element_spans
from docmill.tools.scope import (
    DOCUMENT_PART,
    SHAPE_TAGS,
    ScopeError,
    numbered,
    parse_cell,
    shape_key,
    sheet_parts,
    slide_parts,
)

Parts = dict[str, bytes]
Editor = Callable[[str, Parts, dict[str, Any], dict[str, Any]], Parts]
_START = re.compile(r"<([^\s/>]+)(?:\"[^\"]*\"|'[^']*'|[^'\">])*>")


def _start_tag(fragment: str) -> tuple[str, str]:
    """요소 조각의 여는 태그(자기 닫힘이면 여는 태그로 바꿈)와 접두사(`a:` 등)."""
    m = _START.match(fragment)
    if m is None:
        raise OoxmlError(f"요소를 해석하지 못했다: {fragment[:40]}")
    tag = m.group(0)
    if tag.endswith("/>"):
        tag = tag[:-2].rstrip() + ">"
    name = m.group(1)
    return tag, name[: name.index(":") + 1] if ":" in name else ""


def _first(fragment: str, want: Callable[[str, tuple[str, ...]], bool]) -> str:
    spans = element_spans(fragment, want)
    return fragment[spans[0].start : spans[0].end] if spans else ""


def _replace(text: str, span: Span, new: str) -> str:
    return text[: span.start] + new + text[span.end :]


def _paragraph(start: str, pre: str, ppr: str, rpr: str, line: str, keep: bool) -> str:
    space = ' xml:space="preserve"' if keep else ""
    run = f"<{pre}r>{rpr}<{pre}t{space}>{escape(line)}</{pre}t></{pre}r>"
    return f"{start}{ppr}{run}</{pre}p>"


def set_shape_text(shape: str, text: str) -> str:
    """도형의 글자를 바꾼다. 첫 문단의 문단·글자 서식을 모든 줄에 쓴다."""
    paragraphs = element_spans(shape, lambda n, p: n == "p" and p[-1:] == ("txBody",))
    if not paragraphs:
        raise ScopeError("글자를 넣을 수 없는 도형이다")
    if len(element_spans(shape, lambda n, p: n == "txBody")) != 1:
        raise ScopeError("글자 상자가 하나인 도형만 고칠 수 있다")
    first = shape[paragraphs[0].start : paragraphs[0].end]
    start, pre = _start_tag(first)
    ppr = _first(first, lambda n, p: n == "pPr" and len(p) == 1)
    rpr = _first(first, lambda n, p: n == "rPr" and p[1:] == ("r",))
    body = "".join(_paragraph(start, pre, ppr, rpr, line, False) for line in text.split("\n"))
    return shape[: paragraphs[0].start] + body + shape[paragraphs[-1].end :]


def _part_text(parts: Parts, name: str) -> str:
    try:
        return parts[name].decode("utf-8")
    except (KeyError, UnicodeDecodeError) as exc:
        raise ScopeError(f"{name}을(를) 읽지 못했다") from exc


def _set_text(parts: Parts, items: Any) -> None:
    if not isinstance(items, list) or not items:
        raise ScopeError("set_text는 {slide, shape, text} 목록이어야 한다")
    slides = slide_parts(parts)
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ScopeError(f"set_text 항목이 올바르지 않다: {item}")
        name = numbered(slides, item.get("slide"), "슬라이드")
        shape_id = item.get("shape")
        text = _part_text(parts, name)
        spans = element_spans(text, lambda n, p: n in SHAPE_TAGS and p[-1:] == ("spTree",))
        wanted = str(shape_id)
        span = next(
            (s for i, s in enumerate(spans) if shape_key(text[s.start : s.end], i) == wanted), None
        )
        if span is None:
            raise ScopeError(f"슬라이드 {item['slide']}에 도형 {shape_id}가 없다")
        new = set_shape_text(text[span.start : span.end], item["text"])
        parts[name] = _replace(text, span, new).encode("utf-8")


def cell_xml(cell: str, value: Any) -> str:
    """셀 요소를 새 값으로 바꾼다. 스타일(s) 등 다른 속성은 그대로 둔다."""
    start, pre = _start_tag(cell)
    start = re.sub(r"\st=\"[^\"]*\"", "", start)
    if value is None:
        return start[:-1] + "/>"
    if isinstance(value, bool):
        kind, inner = "b", f"<{pre}v>{int(value)}</{pre}v>"
    elif isinstance(value, int | float):
        kind, inner = "n", f"<{pre}v>{value!r}</{pre}v>"
    elif isinstance(value, str):
        text = f'<{pre}t xml:space="preserve">{escape(value)}</{pre}t>'
        kind, inner = "inlineStr", f"<{pre}is>{text}</{pre}is>"
    else:
        raise ScopeError(f"셀에 넣을 수 없는 값이다: {value!r}")
    return f'{start[:-1]} t="{kind}">{inner}</{pre}c>'


def _set_cells(parts: Parts, cells: Any, sheet: Any) -> None:
    if not isinstance(cells, dict) or not cells:
        raise ScopeError("set_cells는 {셀 주소: 값} 객체여야 한다")
    name = numbered(sheet_parts(parts), sheet, "시트")
    text = _part_text(parts, name)
    for ref in sorted(cells):
        address = str(ref).upper()
        parse_cell(address)
        pattern = re.compile(rf"<[^>]*?\sr=\"{address}\"")
        spans = element_spans(text, lambda n, p: n == "c" and p[-1:] == ("row",))
        span = next((s for s in spans if pattern.match(text, s.start)), None)
        if span is None:
            raise ScopeError(f"시트 {sheet}에 셀 {address}가 없다 (값이 있는 셀만 고칠 수 있다)")
        text = _replace(text, span, cell_xml(text[span.start : span.end], cells[ref]))
    parts[name] = text.encode("utf-8")


def paragraph_xml(paragraph: str, text: str) -> str:
    """문단 글자를 바꾼다. 문단 서식과 첫 글자 서식은 그대로 둔다."""
    start, pre = _start_tag(paragraph)
    ppr = _first(paragraph, lambda n, p: n == "pPr" and len(p) == 1)
    rpr = _first(paragraph, lambda n, p: n == "rPr" and p[1:] == ("r",))
    return _paragraph(start, pre, ppr, rpr, text, True)


def _set_paragraphs(parts: Parts, items: Any) -> None:
    if not isinstance(items, dict) or not items:
        raise ScopeError("set_paragraphs는 {문단 번호: 문장} 객체여야 한다")
    text = _part_text(parts, DOCUMENT_PART)
    spans = element_spans(text, lambda n, p: n == "p" and p[-1:] == ("body",))
    replaced: dict[int, str] = {}
    for key, value in items.items():
        if not str(key).isdigit() or int(key) >= len(spans) or not isinstance(value, str):
            raise ScopeError(f"문단 {key}을(를) 고칠 수 없다 (0~{len(spans) - 1}, 값은 문자열)")
        span = spans[int(key)]
        replaced[int(key)] = paragraph_xml(text[span.start : span.end], value)
    for index in sorted(replaced, reverse=True):
        text = _replace(text, spans[index], replaced[index])
    parts[DOCUMENT_PART] = text.encode("utf-8")


_OPS = {".pptx": {"set_text"}, ".xlsx": {"set_cells", "sheet"}, ".docx": {"set_paragraphs"}}


def apply_edit(kind: str, parts: Parts, scope: dict[str, Any], edit: dict[str, Any]) -> Parts:
    """편집 내용을 적용한 새 파트 사전을 돌려준다 (원본 사전은 바꾸지 않는다)."""
    if not isinstance(edit, dict) or not edit:
        raise ScopeError("edit는 비어 있지 않은 객체여야 한다")
    extra = sorted(set(edit) - _OPS.get(kind, set()))
    if extra:
        raise ScopeError(f"{kind}에 쓸 수 없는 편집: {', '.join(extra)}")
    edited = dict(parts)
    if kind == ".pptx":
        _set_text(edited, edit.get("set_text"))
    elif kind == ".xlsx":
        _set_cells(edited, edit.get("set_cells"), edit.get("sheet", scope.get("sheet", 1)))
    else:
        _set_paragraphs(edited, edit.get("set_paragraphs"))
    return edited
