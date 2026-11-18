"""`review_map` 업무 도구 (FR-33).

문서에서 사람이 확인해야 할 곳만 골라 위치와 이유로 돌려준다. 이유는 세 가지다.

- `출처 없음`: 문서 옆 `<문서 이름>.provenance.json`(FR-21)에 기록되지 않은 숫자
- `근거 없는 예측`: 예측·평가 표현('예상', '전망', '증가할', '최고' 등)이 있는데
  출처 있는 숫자가 없는 문장
- `이번에 바뀜`: 기준 버전(baseline)과 비교해 바뀌거나 새로 생기거나 사라진 문단·그래프 값

규칙 기반이며(AI·엔진·시각·난수 없음) 같은 입력이면 같은 목록을 같은 순서(문서 순서)로 돌려준다.
지원 형식은 pptx(문단·그래프 값)와 docx(문단)다.
"""

from __future__ import annotations

import difflib
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from docmill.runlog import current_run_id, new_run_id
from docmill.tools.hwp import HwpError, is_hwp, read_hwp_lines
from docmill.tools.provenance import _points, _rels, provenance_path, slide_parts

UNSOURCED = "출처 없음"
UNFOUNDED = "근거 없는 예측"
CHANGED = "이번에 바뀜"

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_SLIDE_PART = re.compile(r"ppt/slides/slide(\d+)\.xml")
# A number that is not glued to a preceding word (so "영업1팀", "Q3" are not numbers).
_NUMBER = re.compile(r"(?<![\w.,])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")
_PREDICTION_WORDS = (
    "예상",
    "전망",
    "예측",
    "증가할",
    "감소할",
    "늘어날",
    "줄어들",
    "상승할",
    "하락할",
    "최고",
    "최상",
    "최악",
    "기대된",
    "것으로 보인",
    "확실",
)
_TOLERANCE = 1e-9


class ReviewError(Exception):
    """검토 목록을 만들지 못했을 때 실패한 단계(step)와 이유를 담는다."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


@dataclass(frozen=True)
class Segment:
    """문서의 검토 단위 하나 (문단 또는 그래프 값). value는 그래프 값일 때만 있다."""

    kind: str
    text: str
    location: dict[str, Any]
    value: float | None = None


@dataclass
class Provenance:
    """출처 파일에서 읽은 숫자 위치. text는 (슬라이드, 도형) → 값, chart는 그래프 점 → 값."""

    text: dict[tuple[int, str], list[float]] = field(default_factory=dict)
    chart: dict[tuple[str, int, int], float] = field(default_factory=dict)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= _TOLERANCE * max(1.0, abs(b))


def _to_float(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


# ── 문서 읽기 ────────────────────────────────────────────────────────────


def _paragraph_text(paragraph: ET.Element, text_tag: str) -> str:
    ns = text_tag[: text_tag.index("}") + 1]
    breaks = {f"{ns}tab", f"{ns}br"}
    parts = [
        (e.text or "") if e.tag == text_tag else " "
        for e in paragraph.iter()
        if e.tag == text_tag or e.tag in breaks
    ]
    return "".join(parts).strip()


def _cnv_id(shape: ET.Element) -> str:
    c_nv = next(shape.iter(f"{_P}cNvPr"), None)
    return str(c_nv.get("id", "")) if c_nv is not None else ""


def _chart_segments(
    archive: zipfile.ZipFile, chart_part: str, location: dict[str, Any]
) -> list[Segment]:
    if chart_part not in archive.namelist():
        return []
    root = ET.fromstring(archive.read(chart_part))
    chart = Path(chart_part).stem
    segments: list[Segment] = []
    for s_no, ser in enumerate(root.iter(f"{_C}ser"), start=1):
        categories = dict(_points(ser.find(f"{_C}cat")))
        for idx, raw in _points(ser.find(f"{_C}val")):
            label = categories.get(idx)
            text = f"{label}: {raw}" if label else raw
            loc = {**location, "chart": chart, "series": s_no, "point": idx}
            segments.append(Segment("chart", text, loc, _to_float(raw)))
    return segments


def _slide_segments(archive: zipfile.ZipFile, part: str) -> list[Segment]:
    m = _SLIDE_PART.fullmatch(part)
    slide = int(m.group(1)) if m else 0
    root = ET.fromstring(archive.read(part))
    rels = _rels(archive, part)
    segments: list[Segment] = []
    for shape in root.iter():
        if shape.tag not in (f"{_P}sp", f"{_P}graphicFrame"):
            continue
        base = {"slide": slide, "shape": _cnv_id(shape)}
        for p_no, paragraph in enumerate(shape.iter(f"{_A}p"), start=1):
            text = _paragraph_text(paragraph, f"{_A}t")
            if text:
                segments.append(Segment("text", text, {**base, "paragraph": p_no}))
        for chart in shape.iter(f"{_C}chart"):
            target = rels.get(chart.get(f"{_R}id", ""), "")
            if target:
                segments += _chart_segments(archive, target, base)
    return segments


def _pptx_segments(archive: zipfile.ZipFile) -> list[Segment]:
    segments: list[Segment] = []
    for part in slide_parts(archive):
        segments += _slide_segments(archive, part)
    return segments


def _docx_segments(archive: zipfile.ZipFile) -> list[Segment]:
    root = ET.fromstring(archive.read("word/document.xml"))
    segments: list[Segment] = []
    for p_no, paragraph in enumerate(root.iter(f"{_W}p"), start=1):
        text = _paragraph_text(paragraph, f"{_W}t")
        if text:
            segments.append(Segment("text", text, {"paragraph": p_no}))
    return segments


def _hwp_segments(document: Path) -> list[Segment]:
    try:
        lines = read_hwp_lines(document)
    except HwpError as exc:
        raise ReviewError("read", str(exc)) from exc
    return [
        Segment("text", line.strip(), {"paragraph": no})
        for no, line in enumerate(lines, start=1)
        if line.strip()
    ]


_READERS = {".pptx": _pptx_segments, ".docx": _docx_segments}


def read_segments(document: Path) -> list[Segment]:
    """문서의 문단·그래프 값을 문서 순서대로 읽는다."""
    hwp = is_hwp(document)
    reader = _READERS.get(document.suffix.lower())
    if reader is None and not hwp:
        raise ReviewError("open", f"지원하지 않는 형식입니다(pptx·docx·hwp·hwpx만): {document}")
    if not document.is_file():
        raise ReviewError("open", f"파일을 찾을 수 없습니다: {document}")
    if hwp:
        return _hwp_segments(document)
    assert reader is not None
    try:
        with zipfile.ZipFile(document) as archive:
            return reader(archive)
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError) as exc:
        raise ReviewError("read", f"문서를 읽지 못했습니다: {exc}") from exc


def load_provenance(document: Path) -> Provenance:
    """문서 옆 출처 파일을 읽는다. 없으면 빈 출처다."""
    found = Provenance()
    path = provenance_path(document)
    if not path.is_file():
        return found
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data.get("numbers", []):
        loc = item.get("location", {})
        value = float(item["value"])
        if item.get("kind") == "chart" and {"chart", "series", "point"} <= loc.keys():
            found.chart[(str(loc["chart"]), int(loc["series"]), int(loc["point"]))] = value
        elif "slide" in loc and "shape" in loc:
            key = (int(loc["slide"]), str(loc["shape"]))
            found.text.setdefault(key, []).append(value)
    return found


# ── 규칙 ────────────────────────────────────────────────────────────────


def _item(reason: str, text: str, location: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"reason": reason, "text": text, "location": location, **extra}


def _chart_sourced(segment: Segment, prov: Provenance) -> bool:
    loc = segment.location
    expected = prov.chart.get((str(loc["chart"]), int(loc["series"]), int(loc["point"])))
    return expected is not None and segment.value is not None and _close(segment.value, expected)


def _sourced_values(segment: Segment, prov: Provenance) -> list[float]:
    loc = segment.location
    if "slide" not in loc:
        return []
    return prov.text.get((int(loc["slide"]), str(loc["shape"])), [])


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s.strip()]


def _text_items(segment: Segment, prov: Provenance) -> list[dict[str, Any]]:
    sourced = _sourced_values(segment, prov)
    items: list[dict[str, Any]] = []
    for sentence in _sentences(segment.text):
        numbers = [(m.group(0), _to_float(m.group(0))) for m in _NUMBER.finditer(sentence)]
        unsourced = [
            raw for raw, v in numbers if v is None or not any(_close(v, s) for s in sourced)
        ]
        if any(w in sentence for w in _PREDICTION_WORDS) and len(unsourced) == len(numbers):
            items.append(_item(UNFOUNDED, sentence, segment.location))
        for raw in unsourced:
            items.append(_item(UNSOURCED, raw, segment.location, context=sentence))
    return items


def check_segments(segments: list[Segment], prov: Provenance) -> list[dict[str, Any]]:
    """출처 없는 숫자와 근거 없는 예측 문장을 문서 순서대로 고른다."""
    return [item for _, item in _keyed_checks(segments, prov)]


def _keyed_checks(
    segments: list[Segment], prov: Provenance
) -> list[tuple[tuple[int, int], dict[str, Any]]]:
    keyed: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for i, segment in enumerate(segments):
        if segment.kind == "chart":
            found = (
                []
                if _chart_sourced(segment, prov)
                else [_item(UNSOURCED, segment.text, segment.location)]
            )
        else:
            found = _text_items(segment, prov)
        keyed += [((i, 0), item) for item in found]
    return keyed


def _key(segment: Segment) -> tuple[str, str]:
    return (segment.kind, segment.text)


def _keyed_diff(
    current: list[Segment], baseline: list[Segment]
) -> list[tuple[tuple[int, int], dict[str, Any]]]:
    """바뀐 항목과 문서 순서 키. 삭제 항목은 바로 앞 현재 세그먼트 뒤에 놓는다."""
    matcher = difflib.SequenceMatcher(
        a=[_key(s) for s in baseline], b=[_key(s) for s in current], autojunk=False
    )
    keyed: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op == "equal":
            continue
        old, new = baseline[a1:a2], current[b1:b2]
        for offset, segment in enumerate(new):
            extra: dict[str, Any] = {"change": "추가"}
            if offset < len(old):
                extra = {"change": "수정", "before": old[offset].text}
            item = _item(CHANGED, segment.text, segment.location, **extra)
            keyed.append(((b1 + offset, 1), item))
        for segment in old[len(new) :]:
            item = _item(CHANGED, segment.text, segment.location, change="삭제")
            keyed.append(((b2 - 1, 2), item))
    return keyed


def diff_segments(current: list[Segment], baseline: list[Segment]) -> list[dict[str, Any]]:
    """기준 버전과 비교해 바뀐(수정·추가·삭제) 문단·그래프 값을 문서 순서대로 고른다."""
    return [item for _, item in _keyed_diff(current, baseline)]


# ── 도구 ────────────────────────────────────────────────────────────────


def build_review(document: Path, baseline: Path | None = None) -> dict[str, Any]:
    """검토 목록(items)을 만든다. 실패하면 ReviewError를 낸다."""
    segments = read_segments(document)
    result: dict[str, Any] = {}
    try:
        prov = load_provenance(document)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        prov = Provenance()
        result["provenance_error"] = f"출처 파일을 읽지 못했습니다: {exc}"
    keyed = _keyed_checks(segments, prov)
    if baseline is not None:
        keyed += _keyed_diff(segments, read_segments(baseline))
    keyed.sort(key=lambda pair: pair[0])  # stable: document order
    result["items"] = [item for _, item in keyed]
    return result


def _summary(items: list[dict[str, Any]]) -> str:
    counts = [
        (r, sum(1 for i in items if i["reason"] == r)) for r in (UNSOURCED, UNFOUNDED, CHANGED)
    ]
    detail = ", ".join(f"{r} {n}" for r, n in counts if n)
    return f"검토할 곳 {len(items)}곳" + (f" ({detail})" if detail else "")


def review_map(document: str, baseline: str | None = None) -> str:
    """문서에서 사람이 확인해야 할 곳만 골라 위치와 이유로 돌려준다 (규칙 기반, AI 없음).

    document는 pptx·docx 경로, baseline은 비교할 기준 버전 문서 경로(선택)이다. 출처는 문서 옆
    `<문서 이름>.provenance.json`에서 읽는다. 결과 JSON의 items에 reason('출처 없음'·
    '근거 없는 예측'·'이번에 바뀜'), text(해당 숫자·문장), location(슬라이드·도형·문단 또는
    그래프 점)이 문서 순서대로 담긴다. 실패하면 error와 실패한 단계(step)를 담는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        result = build_review(Path(document), Path(baseline) if baseline else None)
        result["summary"] = _summary(result["items"])
    except ReviewError as exc:
        result = {"error": exc.message, "step": exc.step}
    except Exception as exc:  # document failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected"}
    result["run_id"] = run_id
    return json.dumps(result, ensure_ascii=False)
