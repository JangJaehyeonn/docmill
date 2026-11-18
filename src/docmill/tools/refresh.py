"""`refresh_links` 업무 도구 (FR-37): 살아 있는 문서.

문서 옆 `<문서 이름>.provenance.json`(FR-21)을 원본과의 연결로 보고, 원본 엑셀을 다시 집계해
값이 바뀐 숫자만 문서에서 고친다. 그래프 점은 캐시 값을, 본문 문장(합계·비중 문장)은 그 숫자
글자만 바꾸고 나머지 바이트는 그대로 둔다. 고친 뒤에는 provenance.json도 새 값으로 갱신한다.
원본이 바뀌지 않았으면 아무 파일도 쓰지 않는다. AI는 부르지 않으며, 같은 입력이면 같은 결과다.

다시 집계하는 규칙은 `excel_to_report_ppt`(FR-05)와 같다. 같은 값 범위·이름 범위를 쓰는 출처는
한 표로 보고, 이름과 값이 모두 있는 행만 집계한다(이름은 앞뒤 공백을 뗀다). 합계는 이름순으로
더한다. 문서에 보이는 숫자는 보고서와 같은 자릿수로 반올림해 비교·기록한다.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from docmill.runlog import current_run_id, new_run_id
from docmill.tools.ooxml import OoxmlError, Package, element_spans, ordered_targets
from docmill.tools.provenance import dump, provenance_path
from docmill.tools.scope import shape_key

_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RANGE = re.compile(r"\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)")
# A number shown in text: not glued to a word (`영업1팀`), a decimal point or a char reference.
_NUMBER = re.compile(r"(?<![\w.#])\d[\d,]*(?:\.\d+)?")
_V = re.compile(r"(<(?:[\w.-]+:)?v>)([^<]*)(</(?:[\w.-]+:)?v>)")
_IDX = re.compile(r"^<[^>]*?\sidx=\"(\d+)\"")
_TOLERANCE = 1e-9
_ITEM_KEYS = frozenset({"value", "kind", "location", "source"})
_SOURCE_KEYS = frozenset({"file", "sheet", "aggregation", "range"})

Pair = tuple[str, str, str, str]  # (file, sheet, values range, keys range)


class RefreshError(Exception):
    """새로 고침 단계 하나가 실패했을 때 발생한다. `step`은 실패한 단계 이름이다."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


@dataclass(frozen=True)
class Table:
    """같은 값 범위·이름 범위로 다시 집계한 표: 이름별 합계(이름순)와 빠진 행."""

    sums: dict[str, float]
    total: float
    excluded: list[int]


# ---------------------------------------------------------------- reading xlsx


def _cell_value(cell: ET.Element, shared: list[str]) -> Any:
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        inline = cell.find(f"{_S}is")
        return "".join(t.text or "" for t in inline.iter(f"{_S}t")) if inline is not None else ""
    v = cell.find(f"{_S}v")
    if v is None or v.text is None:
        return None
    if kind == "s":
        return shared[int(v.text)]
    if kind == "b":
        return v.text == "1"
    if kind in ("str", "e"):
        return v.text
    return float(v.text)


def _shared_strings(parts: dict[str, bytes]) -> list[str]:
    if "xl/sharedStrings.xml" not in parts:
        return []
    root = ET.fromstring(parts["xl/sharedStrings.xml"])
    return [
        "".join(t.text or "" for t in si.iter(f"{_S}t") if f"{_S}rPh" not in t.tag)
        for si in root.iter(f"{_S}si")
    ]


def read_cells(xlsx: Path, sheet: str) -> dict[str, Any]:
    """엑셀 시트 하나의 셀 값(`C2` → 값)을 읽는다. 수식 셀은 저장된 결과 값을 쓴다."""
    try:
        parts = Package.read(xlsx.read_bytes()).parts
        names = [
            s.get("name", "") for s in ET.fromstring(parts["xl/workbook.xml"]).iter(f"{_S}sheet")
        ]
        targets = ordered_targets(parts, "xl/workbook.xml", f"{_S}sheet")
        if sheet not in names:
            raise RefreshError("source", f"원본에 '{sheet}' 시트가 없다: {xlsx}")
        root = ET.fromstring(parts[targets[names.index(sheet)]])
        shared = _shared_strings(parts)
        return {c.get("r", ""): _cell_value(c, shared) for c in root.iter(f"{_S}c")}
    except RefreshError:
        raise
    except (OSError, KeyError, IndexError, ValueError, OoxmlError, ET.ParseError) as exc:
        raise RefreshError("source", f"원본 엑셀을 읽지 못했다: {xlsx} ({exc})") from exc


# ---------------------------------------------------------------- re-aggregation


def _span(rng: str) -> tuple[str, int, int]:
    m = _RANGE.fullmatch(rng.strip())
    if m is None:
        raise RefreshError("provenance", f"셀 범위를 해석하지 못했다: {rng!r}")
    return m.group(1), int(m.group(2)), int(m.group(4))


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _number(value: Any, address: str) -> float:
    if isinstance(value, bool):
        raise RefreshError("aggregate", f"{address} 값이 숫자가 아니다: {value!r}")
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        raise RefreshError("aggregate", f"{address} 값이 숫자가 아니다: {value!r}") from None


def aggregate(cells: dict[str, Any], values: str, keys: str) -> Table:
    """값 범위·이름 범위로 이름별 합계를 다시 낸다 (`excel_to_report_ppt`와 같은 규칙)."""
    v_col, first, last = _span(values)
    k_col = _span(keys)[0]
    sums: dict[str, float] = {}
    excluded: list[int] = []
    for row in range(first, last + 1):
        key, raw = cells.get(f"{k_col}{row}"), cells.get(f"{v_col}{row}")
        if _blank(key) or _blank(raw):
            if _blank(key) != _blank(raw):
                excluded.append(row)
            continue
        name = str(key).strip()
        sums[name] = sums.get(name, 0.0) + _number(raw, f"{v_col}{row}")
    ordered = {name: sums[name] for name in sorted(sums)}
    return Table(ordered, sum(ordered.values()), excluded)


def _pairs(numbers: list[dict[str, Any]]) -> dict[tuple[str, str, str], Pair]:
    """출처 범위(값 범위나 이름 범위) → 같은 표의 (파일, 시트, 값 범위, 이름 범위)."""
    found: dict[tuple[str, str, str], Pair] = {}
    for item in numbers:
        src = item["source"]
        if "criteria_range" in src:
            pair = (src["file"], src["sheet"], src["range"], src["criteria_range"])
            found.setdefault((src["file"], src["sheet"], src["range"]), pair)
            found.setdefault((src["file"], src["sheet"], src["criteria_range"]), pair)
    return found


def _literal(src: dict[str, Any], cells: dict[str, Any]) -> float:
    """같은 표를 찾지 못한 sum·count_distinct 출처를 범위 그대로 계산한다."""
    col, first, last = _span(src["range"])
    skip = set(src.get("excluded_rows", []))
    rows = [r for r in range(first, last + 1) if r not in skip]
    raw = [cells.get(f"{col}{r}") for r in rows]
    if src["aggregation"] == "count_distinct":
        return float(len({str(v).strip() for v in raw if not _blank(v)}))
    return sum(_number(v, f"{col}{r}") for v, r in zip(raw, rows, strict=True) if not _blank(v))


def _value(src: dict[str, Any], table: Table | None, cells: dict[str, Any]) -> float:
    agg = src["aggregation"]
    if table is None:
        if agg not in ("sum", "count_distinct"):
            raise RefreshError("provenance", f"이름 범위가 없는 {agg} 출처다")
        return _literal(src, cells)
    if agg == "sum":
        return table.total
    if agg == "count_distinct":
        return float(len(table.sums))
    picked = table.sums.get(str(src.get("criteria", "")).strip(), 0.0)
    if agg == "sumif":
        return picked
    if agg == "share":
        return picked / table.total * 100 if table.total else 0.0
    raise RefreshError("provenance", f"알 수 없는 집계 방식이다: {agg!r}")


# ---------------------------------------------------------------- shown numbers


def _display(number: float) -> int | float:
    return int(number) if number == int(number) else number


def _shown(src: dict[str, Any], value: float) -> tuple[float, dict[str, Any]]:
    """문서에 보이는 값(보고서와 같은 반올림)과 자릿수를 고친 출처."""
    agg = src["aggregation"]
    if agg == "share":
        return float(f"{value:.1f}"), src
    if agg == "count_distinct" or "digits" not in src:
        return value, src
    digits = 0 if value == int(value) else 2
    return float(f"{value:.{digits}f}"), {**src, "digits": digits}


def _text(src: dict[str, Any], shown: float) -> str:
    """본문에 쓸 숫자 글자 (보고서의 표시 형식과 같다)."""
    agg = src["aggregation"]
    if agg == "share":
        return f"{shown:.1f}"
    if agg == "count_distinct":
        return f"{shown:.0f}"
    return f"{shown:,.{int(src.get('digits', 0))}f}"


def _chart_text(number: float) -> str:
    return str(int(number)) if number == int(number) else repr(number)


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= _TOLERANCE * max(1.0, abs(b))


# ---------------------------------------------------------------- document edits


def _replace_all(text: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, new in sorted(edits, reverse=True):
        text = text[:start] + new + text[end:]
    return text


def _shape_span(slide: str, shape: str, label: str) -> tuple[int, int]:
    spans = element_spans(slide, lambda n, p: n in ("sp", "graphicFrame"))
    for index, span in enumerate(spans):
        if shape_key(slide[span.start : span.end], index) == shape:
            return span.start, span.end
    raise RefreshError("locate", f"{label}을(를) 문서에서 찾지 못했다")


def _tokens(slide: str, start: int, end: int) -> list[tuple[int, int, float]]:
    """도형 안 글자(`a:t`)의 숫자 위치와 값을 문서 순서로 돌려준다."""
    fragment = slide[start:end]
    found: list[tuple[int, int, float]] = []
    for t in element_spans(fragment, lambda n, p: n == "t" and p[-1:] in (("r",), ("fld",))):
        inner = fragment[t.start : t.end]
        open_end = inner.index(">") + 1
        if inner.endswith("/>"):
            continue
        body_end = inner.rindex("</")
        for m in _NUMBER.finditer(inner, open_end, body_end):
            at = start + t.start + m.start()
            found.append((at, at + len(m.group(0)), float(m.group(0).replace(",", ""))))
    return found


def _shape_edits(
    slide: str, label: str, shape: str, items: list[tuple[dict[str, Any], str | None]]
) -> list[tuple[int, int, str]]:
    """도형 하나의 숫자를 provenance 순서대로 짝지어, 바뀐 숫자의 글자 교체 목록을 만든다."""
    start, end = _shape_span(slide, shape, label)
    tokens = _tokens(slide, start, end)
    edits: list[tuple[int, int, str]] = []
    cursor = 0
    for item, new_text in items:
        old = float(item["value"])
        while cursor < len(tokens) and not _same(tokens[cursor][2], old):
            cursor += 1
        if cursor == len(tokens):
            raise RefreshError("locate", f"{label}에서 숫자 {_display(old)}을(를) 찾지 못했다")
        if new_text is not None:
            edits.append((tokens[cursor][0], tokens[cursor][1], new_text))
        cursor += 1
    return edits


def _point_edit(chart: str, series: int, point: int, new: str, label: str) -> tuple[int, int, str]:
    sers = element_spans(chart, lambda n, p: n == "ser")
    if not 1 <= series <= len(sers):
        raise RefreshError("locate", f"{label}의 계열을 찾지 못했다")
    ser = sers[series - 1]
    vals = element_spans(chart[ser.start : ser.end], lambda n, p: n == "val")
    if not vals:
        raise RefreshError("locate", f"{label}의 값 목록을 찾지 못했다")
    base = ser.start + vals[0].start
    val = chart[base : ser.start + vals[0].end]
    for position, pt in enumerate(element_spans(val, lambda n, p: n == "pt")):
        frag = val[pt.start : pt.end]
        m = _IDX.match(frag)
        if (int(m.group(1)) if m else position) != point:
            continue
        v = _V.search(frag)
        if v is None:
            break
        at = base + pt.start + v.start(2)
        return at, at + len(v.group(2)), new
    raise RefreshError("locate", f"{label}의 점을 찾지 못했다")


# ---------------------------------------------------------------- refresh


def _label(item: dict[str, Any]) -> str:
    loc, src = item["location"], item["source"]
    where = f"슬라이드 {loc.get('slide')}"
    if item["kind"] == "chart":
        where += f" 그래프 {loc.get('chart')} 계열 {loc.get('series')} 점 {loc.get('point')}"
    else:
        where += f" 도형 {loc.get('shape')}"
    what = {
        "sum": "합계",
        "count_distinct": "항목 수",
        "sumif": f"{src.get('criteria', '')} 합계",
        "share": f"{src.get('criteria', '')} 비중(%)",
    }.get(src["aggregation"], src["aggregation"])
    return f"{where} · {what}"


def _source_path(file: str, document: Path) -> Path:
    path = Path(file)
    if not path.is_absolute() and not path.is_file():
        path = document.parent / path
    if not path.is_file():
        raise RefreshError("source", f"원본 파일이 없다: {file}")
    return path


class _Sources:
    """원본 셀과 다시 집계한 표를 파일·시트·범위별로 한 번씩만 읽고 계산한다."""

    def __init__(self, document: Path, numbers: list[dict[str, Any]]) -> None:
        self._document = document
        self._pairs = _pairs(numbers)
        self._cells: dict[tuple[str, str], dict[str, Any]] = {}
        self._tables: dict[Pair, Table] = {}

    def cells(self, file: str, sheet: str) -> dict[str, Any]:
        key = (file, sheet)
        if key not in self._cells:
            self._cells[key] = read_cells(_source_path(file, self._document), sheet)
        return self._cells[key]

    def table(self, src: dict[str, Any]) -> Table | None:
        pair = self._pairs.get((src["file"], src["sheet"], src["range"]))
        if pair is None:
            return None
        if pair not in self._tables:
            self._tables[pair] = aggregate(self.cells(pair[0], pair[1]), pair[2], pair[3])
        return self._tables[pair]

    def tables(self) -> dict[Pair, Table]:
        return self._tables


def _renewed(item: dict[str, Any], sources: _Sources) -> tuple[dict[str, Any], float, str]:
    """출처 항목 하나를 다시 집계해 (새 항목, 보이는 값, 문서에 쓸 글자)를 돌려준다."""
    src = item["source"]
    table = sources.table(src)
    value = _value(src, table, sources.cells(src["file"], src["sheet"]))
    shown, new_src = _shown(src, value)
    if table is not None:
        new_src = {k: v for k, v in new_src.items() if k != "excluded_rows"}
        if table.excluded:
            new_src["excluded_rows"] = list(table.excluded)
    new_item = {**item, "value": _display(shown), "source": new_src}
    text = _chart_text(shown) if item["kind"] == "chart" else _text(new_src, shown)
    return new_item, shown, text


def _warnings(numbers: list[dict[str, Any]], sources: _Sources) -> list[str]:
    """숫자만 바꿔서는 맞출 수 없는 곳(최대 항목 변경, 문서에 없는 새 항목)을 알린다."""
    found: set[str] = set()
    for pair, table in sources.tables().items():
        named = [n for n in numbers if "criteria" in n["source"] and _table_of(n) == pair[:3]]
        names = {_criteria(n) for n in named}
        new = [name for name in table.sums if name not in names]
        if new:
            found.add(f"원본에 문서에 없는 항목이 생겼다: {', '.join(new)}")
        gone = sorted(name for name in names if name not in table.sums)
        if gone:
            found.add(f"원본에서 사라진 항목은 0으로 고쳤다: {', '.join(gone)}")
        if not table.sums:
            continue
        top = max(table.sums, key=lambda name: (table.sums[name], name))
        for n in named:
            is_top_line = n["kind"] == "summary" and n["source"]["aggregation"] == "sumif"
            if is_top_line and _criteria(n) != top:
                slide = n["location"].get("slide")
                found.add(
                    f"요약의 최대 항목은 이제 {top}이다. 슬라이드 {slide}의 문장을 확인하세요"
                )
    return sorted(found)


def _table_of(item: dict[str, Any]) -> tuple[str, str, str]:
    src = item["source"]
    return src["file"], src["sheet"], src["range"]


def _criteria(item: dict[str, Any]) -> str:
    return str(item["source"].get("criteria", "")).strip()


def _load(document: Path) -> list[dict[str, Any]]:
    if document.suffix.lower() != ".pptx" or not document.is_file():
        raise RefreshError("input", f"읽을 수 있는 .pptx 파일이 아니다: {document}")
    path = provenance_path(document)
    if not path.is_file():
        raise RefreshError("provenance", f"원본 연결(출처 파일)이 없다: {path.name}")
    try:
        numbers = json.loads(path.read_text(encoding="utf-8"))["numbers"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RefreshError("provenance", f"출처 파일을 읽지 못했다: {exc}") from exc
    if not isinstance(numbers, list) or not all(_well_formed(item) for item in numbers):
        raise RefreshError("provenance", f"출처 파일 형식이 맞지 않는다: {path.name}")
    return numbers


def _well_formed(item: Any) -> bool:
    if not isinstance(item, dict) or not item.keys() >= _ITEM_KEYS:
        return False
    src, loc = item["source"], item["location"]
    if not isinstance(src, dict) or not isinstance(loc, dict) or not src.keys() >= _SOURCE_KEYS:
        return False
    if isinstance(item["value"], bool) or not isinstance(item["value"], int | float):
        return False
    need = {"slide", "chart", "series", "point"} if item["kind"] == "chart" else {"slide", "shape"}
    return need <= loc.keys()


def _edit_document(
    parts: dict[str, bytes], numbers: list[dict[str, Any]], texts: list[str | None]
) -> dict[str, bytes]:
    """바뀐 숫자(texts가 None이 아닌 항목)만 고친 파트들을 돌려준다."""
    shapes: dict[tuple[int, str], list[tuple[dict[str, Any], str | None]]] = {}
    charts: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for item, text in zip(numbers, texts, strict=True):
        loc = item["location"]
        if item["kind"] == "chart":
            if text is not None:
                charts.setdefault(str(loc["chart"]), []).append((item, text))
        else:
            shapes.setdefault((int(loc["slide"]), str(loc["shape"])), []).append((item, text))
    changed: dict[str, bytes] = {}
    edits: dict[str, list[tuple[int, int, str]]] = {}
    for (slide, shape), items in shapes.items():
        if all(text is None for _, text in items):
            continue
        part = f"ppt/slides/slide{slide}.xml"
        label = f"슬라이드 {slide} 도형 {shape}"
        edits.setdefault(part, []).extend(_shape_edits(_xml(parts, part), label, shape, items))
    for chart, points in charts.items():
        part = f"ppt/charts/{chart}.xml"
        for item, text in points:
            loc = item["location"]
            point = _point_edit(
                _xml(parts, part), int(loc["series"]), int(loc["point"]), text, _label(item)
            )
            edits.setdefault(part, []).append(point)
    for part, part_edits in edits.items():
        changed[part] = _replace_all(_xml(parts, part), part_edits).encode("utf-8")
    return changed


def _xml(parts: dict[str, bytes], part: str) -> str:
    if part not in parts:
        raise RefreshError("locate", f"문서에 {part}가 없다")
    return parts[part].decode("utf-8")


def _publish(data: bytes, final: Path) -> None:
    """같은 폴더의 임시 파일에 쓴 뒤 원자적으로 교체한다. 실패하면 임시 파일을 지운다."""
    partial = final.with_name(f".{final.name}.part")
    try:
        partial.write_bytes(data)
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _refresh(document: Path, run_id: str) -> dict[str, Any]:
    numbers = _load(document)
    sources = _Sources(document, numbers)
    renewed: list[dict[str, Any]] = []
    texts: list[str | None] = []
    changes: list[dict[str, Any]] = []
    for item in numbers:
        new_item, shown, text = _renewed(item, sources)
        renewed.append(new_item)
        moved = not _same(shown, float(item["value"]))
        texts.append(text if moved else None)
        if moved:
            changes.append(
                {
                    "location": item["location"],
                    "kind": item["kind"],
                    "label": _label(item),
                    "old": item["value"],
                    "new": new_item["value"],
                }
            )
    if changes:
        try:
            package = Package.read(document.read_bytes())
            changed = _edit_document(package.parts, numbers, texts)
        except (OoxmlError, ValueError, KeyError) as exc:
            raise RefreshError("locate", f"문서를 고치지 못했다: {exc}") from exc
        _publish(package.write({**package.parts, **changed}), document)
    if renewed != numbers:
        _publish(dump(renewed), provenance_path(document))
    summary = (
        f"숫자 {len(changes)}곳을 새 원본 값으로 고쳤다"
        if changes
        else "원본이 바뀌지 않아 고칠 숫자가 없다"
    )
    return {
        "document": str(document),
        "provenance": str(provenance_path(document)),
        "changes": changes,
        "warnings": _warnings(numbers, sources),
        "summary": summary,
        "run_id": run_id,
    }


def refresh_links(document: str) -> str:
    """문서의 숫자를 연결된 원본 엑셀의 최신 값으로 고친다 (살아 있는 문서).

    document는 `excel_to_report_ppt`로 만든 pptx 경로다. 문서 옆
    `<문서 이름>.provenance.json`의 출처(원본 파일·시트·셀 범위·집계 방식)로 원본을 다시
    집계해, 값이 바뀐 그래프 점과 합계·비중 문장의 숫자만 문서 자리에서 고치고
    provenance.json도 새 값으로 갱신한다. 결과 JSON의 changes는 바뀐 위치 목록
    (location·label·old·new)이고, 원본이 그대로면 빈 목록이며 파일은 바이트 단위로 그대로다.
    숫자만 바꿔서는 맞출 수 없는 곳(요약의 최대 항목 변경, 원본에 새로 생긴 항목)은
    warnings에 담는다. 실패하면 error와 실패한 단계(step)를 담는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        result = _refresh(Path(document), run_id)
    except RefreshError as exc:
        result = {"error": exc.message, "step": exc.step, "run_id": run_id}
    except Exception as exc:  # file failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected", "run_id": run_id}
    return json.dumps(result, ensure_ascii=False)
