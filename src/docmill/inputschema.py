"""레시피 입력 구조 기록과 변화 감지 (FR-22).

레시피를 저장할 때 `$input` 엑셀의 시트 이름·열 이름·열 형식을 `input_schema`로 함께 남긴다.
실행할 때 열 구조가 달라졌으면 규칙(같은 위치·같은 형식·비슷한 이름)만으로 열 매핑을 제안하고,
사용자가 준 매핑(`새 열=원래 열`)은 엑셀을 읽을 때 머리글 이름을 바꾸는 방식으로 적용한다.
AI·네트워크를 쓰지 않으며, 같은 입력이면 같은 제안이 나온다.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from docmill.engines import get_tool
from docmill.engines.base import EngineResult

# Column type labels stored in a recipe.
NUMBER, TEXT, BOOLEAN, MIXED, EMPTY = "number", "text", "boolean", "mixed", "empty"
_TYPE_LABEL = {NUMBER: "숫자", TEXT: "문자", BOOLEAN: "참/거짓", MIXED: "혼합", EMPTY: "빈 열"}
_NAME_SIMILARITY = 0.5

# Active column map for this run: new column name -> original column name.
_column_map: ContextVar[dict[str, str] | None] = ContextVar("docmill_column_map", default=None)


class SchemaError(Exception):
    """입력 구조를 읽거나 매핑을 적용하지 못했을 때 발생한다."""


def _cell_type(value: Any) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return EMPTY
    if isinstance(value, bool):
        return BOOLEAN
    if isinstance(value, int | float):
        return NUMBER
    return TEXT


def column_type(values: list[Any]) -> str:
    """열 값들의 형식(number·text·boolean·mixed·empty)을 정한다. 빈 칸은 무시한다."""
    kinds = sorted({_cell_type(v) for v in values} - {EMPTY})
    if not kinds:
        return EMPTY
    return kinds[0] if len(kinds) == 1 else MIXED


def header_names(rows: list[list[Any]]) -> list[str]:
    """첫 행을 머리글로 보고 열 이름 목록을 돌려준다."""
    return [str(c).strip() if c is not None else "" for c in (rows[0] if rows else [])]


def schema_from_rows(sheets: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    """시트 이름 목록과 첫 시트 값으로 input_schema(sheets·columns·column_types)를 만든다."""
    columns = header_names(rows)
    types: dict[str, str] = {}
    for index, name in enumerate(columns):
        if name and name not in types:
            cells = [row[index] if index < len(row) else None for row in rows[1:]]
            types[name] = column_type(cells)
    return {
        "sheets": list(sheets),
        "columns": [name for name in columns if name],
        "column_types": types,
    }


def _engine_data(result: EngineResult, step: str) -> dict[str, Any]:
    if not result.ok:
        raise SchemaError(f"{step}: {result.message}")
    try:
        data = json.loads(result.output) if isinstance(result.output, str) else result.output
    except ValueError as exc:
        raise SchemaError(f"{step}: 엔진 응답을 해석하지 못했다: {exc}") from exc
    return data if isinstance(data, dict) else {}


def read_input_schema(source: Path) -> dict[str, Any]:
    """엑셀 파일의 시트 이름·첫 시트 열 이름·열 형식을 GridCraft 엔진으로 읽는다."""
    with tempfile.TemporaryDirectory(prefix="docmill-schema-") as tmp:
        workdir = Path(tmp).resolve()
        staged = workdir / "input.xlsx"
        shutil.copyfile(source, staged)
        with get_tool(staged, workdir) as grid:
            opened = _engine_data(grid.open(staged), "open_workbook")
            data = _engine_data(grid.call_tool("read_range", {}), "read_range")
    sheets = opened.get("sheets")
    rows = data.get("values")
    return schema_from_rows(
        [str(s) for s in sheets] if isinstance(sheets, list) else [],
        rows if isinstance(rows, list) else [],
    )


def parse_column_map(items: list[str]) -> dict[str, str]:
    """`새 열=원래 열` 형식 목록을 dict로 바꾼다. 형식이 틀리거나 겹치면 SchemaError."""
    mapping: dict[str, str] = {}
    for item in items:
        new, sep, original = item.partition("=")
        new, original = new.strip(), original.strip()
        if not sep or not new or not original:
            raise SchemaError(f"매핑 형식은 '새 열=원래 열'이어야 한다: {item!r}")
        if new in mapping:
            raise SchemaError(f"같은 열을 두 번 매핑했다: {new!r}")
        mapping[new] = original
    if len(set(mapping.values())) != len(mapping):
        raise SchemaError("서로 다른 열을 같은 원래 열로 매핑할 수 없다")
    return mapping


def apply_map_to_columns(columns: list[str], mapping: Mapping[str, str]) -> list[str]:
    """열 이름 목록에 매핑을 적용한다. 매핑할 열이 없거나 결과 이름이 겹치면 SchemaError."""
    absent = [new for new in sorted(mapping) if new not in columns]
    if absent:
        names = ", ".join(f"'{name}'" for name in absent)
        raise SchemaError(f"매핑할 열이 입력 파일에 없다: {names}")
    if not mapping:
        return list(columns)
    mapped = [mapping.get(name, name) for name in columns]
    # Only clashes the mapping itself creates; pre-existing duplicates are left alone.
    clashes = sorted({name for name in mapping.values() if mapped.count(name) > 1})
    if clashes:
        names = ", ".join(f"'{name}'" for name in clashes)
        raise SchemaError(f"매핑 후 같은 이름의 열이 생긴다: {names}")
    return mapped


@contextmanager
def using_column_map(mapping: Mapping[str, str] | None) -> Iterator[None]:
    """이 블록 안에서 엑셀을 읽을 때 머리글에 열 매핑을 적용한다."""
    token = _column_map.set(dict(mapping) if mapping else None)
    try:
        yield
    finally:
        _column_map.reset(token)


def active_column_map() -> dict[str, str]:
    """지금 적용 중인 열 매핑(새 열 → 원래 열). 없으면 빈 dict."""
    return dict(_column_map.get() or {})


def map_header(rows: list[list[Any]]) -> list[list[Any]]:
    """적용 중인 열 매핑으로 첫 행(머리글)의 열 이름을 바꾼 새 목록을 돌려준다."""
    mapping = _column_map.get()
    if not mapping or not rows:
        return rows
    header = [
        mapping.get(str(cell).strip(), cell) if cell is not None else cell for cell in rows[0]
    ]
    return [header, *rows[1:]]


def _match_reasons(
    original: tuple[int, str],
    candidate: tuple[int, str],
    saved: dict[str, Any],
    actual: dict[str, Any],
) -> tuple[int, list[str]]:
    o_index, o_name = original
    c_index, c_name = candidate
    score, reasons = 0, []
    if o_index == c_index:
        score += 2
        reasons.append(f"같은 위치({o_index + 1}번째 열)")
    o_type = saved.get("column_types", {}).get(o_name, EMPTY)
    c_type = actual.get("column_types", {}).get(c_name, EMPTY)
    if o_type == c_type and o_type != EMPTY:
        score += 1
        reasons.append(f"같은 형식({_TYPE_LABEL.get(o_type, o_type)})")
    ratio = SequenceMatcher(None, o_name, c_name).ratio()
    if ratio >= _NAME_SIMILARITY:
        score += 1
        reasons.append(f"비슷한 이름(유사도 {ratio:.2f})")
    return score, reasons


def suggest_mapping(saved: dict[str, Any], actual: dict[str, Any]) -> list[dict[str, str]]:
    """저장된 구조에서 사라진 열마다 새로 생긴 열 중 가장 그럴듯한 것을 규칙으로 고른다.

    규칙: 같은 위치(+2), 같은 형식(+1), 이름 유사도 0.5 이상(+1). 점수가 높은 쌍부터 한 열씩
    짝짓고, 같은 점수면 원래 열 위치·새 열 위치 순으로 정한다. 결과는 원래 열 순서다.
    """
    saved_cols = [str(c) for c in saved.get("columns", [])]
    actual_cols = [str(c) for c in actual.get("columns", [])]
    missing = [(i, n) for i, n in enumerate(saved_cols) if n not in actual_cols]
    added = [(j, n) for j, n in enumerate(actual_cols) if n not in saved_cols]
    pairs: list[tuple[int, int, int, str, str, list[str]]] = []
    for original in missing:
        for candidate in added:
            score, reasons = _match_reasons(original, candidate, saved, actual)
            if score:
                pairs.append(
                    (-score, original[0], candidate[0], original[1], candidate[1], reasons)
                )
    used_o: set[str] = set()
    used_c: set[str] = set()
    chosen: list[tuple[int, dict[str, str]]] = []
    for _, o_index, _, o_name, c_name, reasons in sorted(pairs, key=lambda p: p[:3]):
        if o_name in used_o or c_name in used_c:
            continue
        used_o.add(o_name)
        used_c.add(c_name)
        entry = {"changed": c_name, "original": o_name, "reason": ", ".join(reasons)}
        entry["map"] = f"{c_name}={o_name}"
        chosen.append((o_index, entry))
    return [entry for _, entry in sorted(chosen, key=lambda item: item[0])]
