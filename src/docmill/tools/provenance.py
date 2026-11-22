"""숫자 출처 추적 (FR-21).

`excel_to_report_ppt`가 만든 pptx의 숫자마다 문서 위치(슬라이드·도형·그래프 점)와 원본 출처
(파일·시트·셀 범위·집계 방식)를 기록한다. 출처는 결과 파일 옆
`<결과 파일 이름>.provenance.json`에 저장한다. 기록은 입력과 문서만으로 정해진다
(시각·난수 없음, 목록은 문서 순서).

집계 방식:
- `sum`: range의 숫자 합
- `sumif`: criteria_range 값이 criteria와 같은 행의 range 값 합
- `count_distinct`: range의 서로 다른 값 개수
- `share`: sumif ÷ sum(range) × 100 (sum(range)이 0이면 0)

그룹 이름은 집계 전에 `strip`으로 앞뒤 공백을 제거한다(`normalize`: "strip"). criteria와
criteria_range·count_distinct 범위의 값 모두 이 규칙을 적용해 비교·계산한다.
합계에 `digits`가 있으면 재계산한 합계를 그 소수 자릿수로 반올림해 문서 숫자와 비교한다.

문장 출처(FR-47): 도구가 만든 문장(요약·상세 줄, 할 일 행)마다 `sentences` 목록에
`text`(문장)·`location`(결과 문서 위치)·`source`(근거: 원본 파일과 셀 범위 또는 문단 순번)를 남긴다.
"""

from __future__ import annotations

import io
import json
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

SUFFIX = ".provenance.json"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_RANGE = re.compile(r"\$?([A-Z]+)\$?(\d+)(?::\$?[A-Z]+\$?\d+)?")
_CHART_REL = "/relationships/chart"
_DIGITS = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# Fixed notices the tools write themselves (no source data behind them, FR-47).
EMPTY_DETAIL = "추가 항목 없음"
TOOL_NOTICES = frozenset({EMPTY_DETAIL})


class ProvenanceError(Exception):
    """문서에서 숫자 위치를 찾지 못해 출처를 기록할 수 없을 때 발생한다."""


@dataclass(frozen=True)
class SheetTable:
    """엑셀에서 읽은 표. `first_row`·`first_col`은 values[0][0]의 1부터 시작하는 위치다."""

    file: str
    sheet: str
    first_row: int
    first_col: int
    values: list[list[Any]]


@dataclass(frozen=True)
class TextNumber:
    """본문에 보이는 숫자 하나. `text`는 그 숫자가 든 본문 전체다."""

    kind: str
    text: str
    value: float
    source: dict[str, Any]


def provenance_path(document: Path) -> Path:
    """문서 옆 출처 파일 경로(`<문서 이름>.provenance.json`)를 돌려준다."""
    return document.with_name(document.name + SUFFIX)


def column_letter(number: int) -> str:
    """1부터 시작하는 열 번호를 엑셀 열 이름(A, B, …, AA)으로 바꾼다."""
    letters = ""
    while number > 0:
        number, rest = divmod(number - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def column_number(letters: str) -> int:
    """엑셀 열 이름을 1부터 시작하는 열 번호로 바꾼다."""
    number = 0
    for ch in letters:
        number = number * 26 + ord(ch) - ord("A") + 1
    return number


def range_origin(address: str) -> tuple[int, int]:
    """`A1:C16` 같은 주소의 왼쪽 위 (행, 열)을 돌려준다. 해석할 수 없으면 (1, 1)."""
    m = _RANGE.fullmatch(address.split("!")[-1].strip()) if address else None
    return (int(m.group(2)), column_number(m.group(1))) if m else (1, 1)


def _display(number: float) -> int | float:
    return int(number) if number == int(number) else number


class Sources:
    """group_by·value 열에서 집계 방식별 출처 정보를 만든다."""

    def __init__(self, table: SheetTable, group_by: str, value: str) -> None:
        header = [str(c).strip() if c is not None else "" for c in table.values[0]]
        first, last = table.first_row + 1, table.first_row + len(table.values) - 1
        last = max(last, first)
        key_col = column_letter(table.first_col + header.index(group_by))
        val_col = column_letter(table.first_col + header.index(value))
        self._base = {"file": table.file, "sheet": table.sheet}
        self._values = f"{val_col}{first}:{val_col}{last}"
        self._keys = f"{key_col}{first}:{key_col}{last}"
        self._excluded = _excluded_rows(table, header.index(group_by), header.index(value))

    def _make(
        self,
        aggregation: str,
        rng: str,
        criteria: str | None = None,
        digits: int | None = None,
    ) -> dict[str, Any]:
        src: dict[str, Any] = {**self._base, "aggregation": aggregation, "range": rng}
        if criteria is not None:
            src["criteria_range"] = self._keys
            src["criteria"] = criteria
        if criteria is not None or aggregation == "count_distinct":
            src["normalize"] = "strip"
        if digits is not None:
            src["digits"] = digits
        if self._excluded:
            src["excluded_rows"] = list(self._excluded)
        return src

    def total(self, digits: int | None = None) -> dict[str, Any]:
        """value 열 전체 합계의 출처. digits는 문서에 보이는 소수 자릿수다."""
        return self._make("sum", self._values, digits=digits)

    def group_total(self, name: str, digits: int | None = None) -> dict[str, Any]:
        """group_by 값 하나의 합계 출처. digits는 문서에 보이는 소수 자릿수다."""
        return self._make("sumif", self._values, name, digits)

    def group_share(self, name: str) -> dict[str, Any]:
        """group_by 값 하나의 비중(%) 출처."""
        return self._make("share", self._values, name)

    def group_count(self) -> dict[str, Any]:
        """group_by 값 개수의 출처."""
        return self._make("count_distinct", self._keys)


def _excluded_rows(table: SheetTable, key_idx: int, val_idx: int) -> list[int]:
    """집계에서 빠진 행(그룹 이름이나 값이 비어 있는 행)의 엑셀 행 번호."""
    rows: list[int] = []
    for offset, row in enumerate(table.values[1:], start=1):
        cells = list(row) + [None] * (max(key_idx, val_idx) + 1 - len(row))
        key, val = cells[key_idx], cells[val_idx]
        key_blank = key is None or not str(key).strip()
        val_blank = val is None or (isinstance(val, str) and not val.strip())
        if (key_blank or val_blank) and not (key_blank and val_blank):
            rows.append(table.first_row + offset)
    return rows


def _rels(archive: zipfile.ZipFile, part: str) -> dict[str, str]:
    """파트의 관계 `Id → 대상 파트 경로`(패키지 기준)."""
    folder, name = posixpath.split(part)
    rels_name = posixpath.join(folder, "_rels", name + ".rels")
    if rels_name not in archive.namelist():
        return {}
    root = ET.fromstring(archive.read(rels_name))
    found: dict[str, str] = {}
    for rel in root.iter(f"{_REL}Relationship"):
        target = rel.get("Target", "")
        found[rel.get("Id", "")] = posixpath.normpath(posixpath.join(folder, target))
    return found


def slide_parts(archive: zipfile.ZipFile) -> list[str]:
    """발표 순서대로 슬라이드 파트 경로를 돌려준다."""
    rels = _rels(archive, "ppt/presentation.xml")
    root = ET.fromstring(archive.read("ppt/presentation.xml"))
    return [rels[s.get(f"{_R}id", "")] for s in root.iter(f"{_P}sldId")]


def _slide_number(part: str) -> int:
    m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", part)
    if not m:
        raise ProvenanceError(f"슬라이드 파트 이름을 해석하지 못했다: {part}")
    return int(m.group(1))


def _shape_text(shape: ET.Element) -> str:
    paragraphs = shape.iter(f"{_A}p")
    return "\n".join("".join(t.text or "" for t in p.iter(f"{_A}t")) for p in paragraphs)


def _shapes(root: ET.Element) -> list[ET.Element]:
    return [el for el in root.iter() if el.tag in (f"{_P}sp", f"{_P}graphicFrame")]


def _shape_id(shape: ET.Element) -> str:
    c_nv = next(shape.iter(f"{_P}cNvPr"), None)
    if c_nv is None or not c_nv.get("id"):
        raise ProvenanceError("도형 id(cNvPr)가 없다")
    return str(c_nv.get("id"))


def _text_entries(
    archive: zipfile.ZipFile, part: str, numbers: list[TextNumber]
) -> list[dict[str, Any]]:
    root = ET.fromstring(archive.read(part))
    shapes = [(s, _shape_text(s)) for s in _shapes(root)]
    entries: list[dict[str, Any]] = []
    for number in numbers:
        shape = next((s for s, text in shapes if number.text and number.text in text), None)
        if shape is None:
            raise ProvenanceError(f"{part}에서 숫자가 든 도형을 찾지 못했다: {number.text!r}")
        location = {"slide": _slide_number(part), "shape": _shape_id(shape)}
        entries.append(_entry(number.value, number.kind, location, number.source))
    return entries


def _chart_frames(archive: zipfile.ZipFile, part: str) -> list[tuple[str, str]]:
    """슬라이드의 그래프 (도형 id, 그래프 파트 경로) 목록을 문서 순서대로 돌려준다."""
    rels = _rels(archive, part)
    root = ET.fromstring(archive.read(part))
    frames: list[tuple[str, str]] = []
    for frame in root.iter(f"{_P}graphicFrame"):
        for chart in frame.iter(f"{_C}chart"):
            target = rels.get(chart.get(f"{_R}id", ""), "")
            if target:
                frames.append((_shape_id(frame), target))
    return frames


def _points(parent: ET.Element | None) -> list[tuple[int, str]]:
    if parent is None:
        return []
    found: list[tuple[int, str]] = []
    for position, pt in enumerate(parent.iter(f"{_C}pt")):
        v = pt.find(f"{_C}v")
        if v is not None and v.text is not None:
            found.append((int(pt.get("idx", position)), v.text))
    return found


def _chart_entries(
    archive: zipfile.ZipFile, part: str, sources: Sources, names: list[str]
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for shape_id, chart_part in _chart_frames(archive, part):
        root = ET.fromstring(archive.read(chart_part))
        for s_no, ser in enumerate(root.iter(f"{_C}ser"), start=1):
            categories = dict(_points(ser.find(f"{_C}cat")))
            for idx, raw in _points(ser.find(f"{_C}val")):
                name = categories.get(idx) or (names[idx] if idx < len(names) else None)
                if name is None or name not in names:
                    raise ProvenanceError(f"그래프 점의 범주를 알 수 없다: {chart_part} {idx}")
                location = {
                    "slide": _slide_number(part),
                    "shape": shape_id,
                    "chart": Path(chart_part).stem,
                    "series": s_no,
                    "point": idx,
                }
                entries.append(_entry(float(raw), "chart", location, sources.group_total(name)))
    return entries


def _entry(
    value: float, kind: str, location: dict[str, Any], source: dict[str, Any]
) -> dict[str, Any]:
    return {"value": _display(value), "kind": kind, "location": location, "source": source}


def collect(
    pptx: Path,
    plan: list[str],
    text_numbers: dict[int, list[TextNumber]],
    sources: Sources,
    names: list[str],
) -> list[dict[str, Any]]:
    """pptx에서 숫자 위치를 찾아 출처 항목 목록을 문서 순서대로 만든다.

    plan은 슬라이드 종류 목록, text_numbers는 슬라이드 순번(0부터) → 본문 숫자다.
    """
    with zipfile.ZipFile(pptx) as archive:
        parts = slide_parts(archive)
        if len(parts) != len(plan):
            raise ProvenanceError(f"슬라이드 수가 계획({len(plan)})과 다르다: {len(parts)}")
        numbers: list[dict[str, Any]] = []
        for index, (kind, part) in enumerate(zip(plan, parts, strict=True)):
            numbers += _text_entries(archive, part, text_numbers.get(index, []))
            if kind == "chart":
                numbers += _chart_entries(archive, part, sources, names)
    return numbers


def dump(numbers: list[dict[str, Any]], sentences: list[dict[str, Any]] | None = None) -> bytes:
    """출처 목록을 provenance.json 바이트로 만든다. 같은 입력이면 바이트 단위로 같다.

    sentences(FR-47 문장 출처)를 주면 `sentences` 키로 함께 저장한다.
    """
    data: dict[str, Any] = {"numbers": numbers}
    if sentences is not None:
        data["sentences"] = sentences
    text = json.dumps(data, ensure_ascii=False, indent=2)
    return (text + "\n").encode("utf-8")


def load_sentences(document: Path) -> list[dict[str, Any]] | None:
    """문서 옆 출처 파일의 문장 출처(`sentences`)를 읽는다. 파일이나 키가 없으면 None.

    출처 파일을 다시 쓰는 도구(새로 고침·범위 넣기)가 문장 출처를 잃지 않게 할 때 쓴다.
    """
    path = provenance_path(document)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    sentences = data.get("sentences") if isinstance(data, dict) else None
    return list(sentences) if isinstance(sentences, list) else None


@dataclass(frozen=True)
class SentenceSpec:
    """도구가 만든 문장 하나와 그 근거. `text`는 문서에 보이는 문장 그대로다."""

    text: str
    source: dict[str, Any]


def collect_sentences(
    pptx: Path, plan: list[str], specs: dict[int, list[SentenceSpec]]
) -> list[dict[str, Any]]:
    """pptx에서 문장이 든 도형을 찾아 문장 출처 항목을 문서 순서대로 만든다 (FR-47).

    specs는 슬라이드 순번(0부터) → 그 슬라이드 본문에 넣은 문장과 근거다.
    """
    with zipfile.ZipFile(pptx) as archive:
        parts = slide_parts(archive)
        if len(parts) != len(plan):
            raise ProvenanceError(f"슬라이드 수가 계획({len(plan)})과 다르다: {len(parts)}")
        sentences: list[dict[str, Any]] = []
        for index, part in enumerate(parts):
            wanted = specs.get(index, [])
            if not wanted:
                continue
            root = ET.fromstring(archive.read(part))
            shapes = [(s, _shape_text(s).split("\n")) for s in root.iter(f"{_P}sp")]
            for spec in wanted:
                shape = next((s for s, lines in shapes if spec.text in lines), None)
                if shape is None:
                    raise ProvenanceError(f"{part}에서 문장이 든 도형을 찾지 못했다: {spec.text!r}")
                location = {"slide": _slide_number(part), "shape": _shape_id(shape)}
                sentences.append({"text": spec.text, "location": location, "source": spec.source})
    return sentences


def sentence_key(text: str) -> str:
    """숫자를 `#`로 바꾼 문장. 원본 값이 바뀌어 숫자만 달라진 문장을 같은 문장으로 본다.

    값의 부호(`-`)도 숫자에 포함해 양수↔음수로 바뀐 문장도 같은 문장으로 본다.
    `항목: 값` 꼴이면 `:` 앞 항목명(예: `1팀`)의 숫자는 식별자로 남기고 값 부분만 바꾼다.
    """
    stripped = text.strip()
    head, sep, value = stripped.partition(":")
    if not sep:
        return _DIGITS.sub("#", stripped)
    return head + sep + _DIGITS.sub("#", value)


def _shape_lines(archive: zipfile.ZipFile) -> dict[tuple[int, str], list[str]]:
    found: dict[tuple[int, str], list[str]] = {}
    names = sorted(n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
    for part in names:
        slide = _slide_number(part)
        for shape in ET.fromstring(archive.read(part)).iter(f"{_P}sp"):
            c_nv = next(shape.iter(f"{_P}cNvPr"), None)
            if c_nv is not None and c_nv.get("id"):
                found[(slide, str(c_nv.get("id")))] = _shape_text(shape).split("\n")
    return found


def retext_sentences(pptx: bytes, sentences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """숫자를 새로 고친 문서에 맞춰 문장 출처의 text를 고친다 (숫자만 바뀐 같은 문장).

    같은 도형에서 숫자를 뺀 모양이 같은 줄을 찾으면 그 줄로 바꾸고, 못 찾으면 그대로 둔다.
    """
    with zipfile.ZipFile(io.BytesIO(pptx)) as archive:
        lines = _shape_lines(archive)
    renewed: list[dict[str, Any]] = []
    used: dict[tuple[int, str], set[int]] = {}
    for item in sentences:
        loc = item.get("location", {}) if isinstance(item, dict) else {}
        text = str(item.get("text", "")) if isinstance(item, dict) else ""
        shape = (int(loc.get("slide", 0)), str(loc.get("shape", "")))
        taken = used.setdefault(shape, set())
        match = _match_line(lines.get(shape, []), text, taken) if text else None
        if match is not None:
            taken.add(match[0])
            item = {**item, "text": match[1]}
        renewed.append(item)
    return renewed


def _match_line(candidates: list[str], text: str, taken: set[int]) -> tuple[int, str] | None:
    """아직 쓰지 않은 줄 중 text와 같은 줄, 없으면 숫자만 다른 첫 줄(문단 순서)을 고른다."""
    free = [(i, c) for i, c in enumerate(candidates) if i not in taken]
    exact = next(((i, c) for i, c in free if c == text), None)
    if exact is not None:
        return exact
    key = sentence_key(text)
    return next(((i, c) for i, c in free if sentence_key(c) == key), None)


def load_chart_sources(document: Path) -> dict[tuple[str, int, int], dict[str, Any]] | None:
    """문서 옆 출처 파일에서 그래프 점 `(그래프, 계열, idx) → 출처`를 읽는다. 없으면 None."""
    path = provenance_path(document)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    found: dict[tuple[str, int, int], dict[str, Any]] = {}
    for item in data.get("numbers", []):
        loc = item.get("location", {})
        if item.get("kind") == "chart" and {"chart", "series", "point"} <= loc.keys():
            key = (str(loc["chart"]), int(loc["series"]), int(loc["point"]))
            found[key] = dict(item["source"])
    return found
