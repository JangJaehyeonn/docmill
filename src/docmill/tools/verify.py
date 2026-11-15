"""`verify_numbers` 업무 도구 (FR-08).

결과 pptx의 그래프 숫자를 원본 엑셀에서 다시 계산한 group_by별 합계와 대조한다. 대조는
입력만으로 정해지며(시각·난수 없음), 엔진은 `DocumentTool` 어댑터로만 호출한다.
"""

from __future__ import annotations

import json
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from docmill.runlog import current_run_id, new_run_id
from docmill.tools.report import Group, ReportError, _read_sheet, aggregate

_C_NS = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_CHART_PART = re.compile(r"ppt/charts/chart\d+\.xml")
_TOLERANCE = 1e-9


def _points(parent: ET.Element | None) -> dict[int, str]:
    """`c:pt` 목록을 `idx → 값 문자열`로 읽는다."""
    found: dict[int, str] = {}
    if parent is None:
        return found
    for position, pt in enumerate(parent.iter(f"{_C_NS}pt")):
        v = pt.find(f"{_C_NS}v")
        if v is not None and v.text is not None:
            found[int(pt.get("idx", position))] = v.text
    return found


def read_chart_points(pptx: Path) -> list[dict[str, Any]]:
    """pptx의 모든 그래프에서 계열별 (위치, 범주, 값)을 문서 순서대로 읽는다."""
    series: list[dict[str, Any]] = []
    with zipfile.ZipFile(pptx) as archive:
        names = sorted(n for n in archive.namelist() if _CHART_PART.fullmatch(n))
        for name in names:
            root = ET.fromstring(archive.read(name))
            for number, ser in enumerate(root.iter(f"{_C_NS}ser"), start=1):
                series.append(
                    {
                        "chart": Path(name).stem,
                        "series": number,
                        "categories": _points(ser.find(f"{_C_NS}cat")),
                        "values": _points(ser.find(f"{_C_NS}val")),
                    }
                )
    return series


def _close(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= _TOLERANCE * max(1.0, abs(expected))


def compare(series: list[dict[str, Any]], groups: list[Group]) -> dict[str, Any]:
    """그래프 값을 그룹 합계와 대조한다. 범주 이름이 있으면 이름으로, 없으면 순서로 짝짓는다."""
    totals = {g.name: g.total for g in groups}
    checked = matched = 0
    mismatches: list[dict[str, Any]] = []
    for ser in series:
        for idx in sorted(ser["values"]):
            raw = ser["values"][idx]
            category = ser["categories"].get(idx)
            if category is None and idx < len(groups):
                category = groups[idx].name
            where = f"{ser['chart']} 계열{ser['series']} '{category}'"
            checked += 1
            expected = totals.get(category) if category is not None else None
            try:
                actual: float | None = float(raw)
            except ValueError:
                actual = None
            if expected is not None and actual is not None and _close(actual, expected):
                matched += 1
                continue
            mismatches.append({"location": where, "expected": expected, "actual": raw})
    return {"checked": checked, "matched": matched, "mismatches": mismatches}


def _verify(document: str, source: str, group_by: str, value: str) -> dict[str, Any]:
    doc, src = Path(document), Path(source)
    if doc.suffix.lower() != ".pptx" or not doc.is_file():
        raise ReportError("document", f"읽을 수 있는 .pptx 파일이 아니다: {document}")
    if src.suffix.lower() != ".xlsx" or not src.is_file():
        raise ReportError("source", f"읽을 수 있는 .xlsx 파일이 아니다: {source}")
    with tempfile.TemporaryDirectory(prefix="docmill-verify-") as tmp:
        groups = aggregate(_read_sheet(src, Path(tmp).resolve()), group_by, value)
    try:
        series = read_chart_points(doc)
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise ReportError("document", f"문서를 읽지 못했다: {exc}") from exc
    if not any(ser["values"] for ser in series):
        raise ReportError("extract", "문서에서 대조할 그래프 숫자를 찾지 못했다")
    return compare(series, groups)


def verify_numbers(document: str, source: str, group_by: str, value: str) -> str:
    """결과 pptx의 그래프 숫자를 원본 엑셀에서 다시 계산한 합계와 대조한다.

    source 엑셀의 group_by 열 기준 value 합계를 계산해 document의 그래프 값과 비교한다.
    결과는 JSON 문자열이며 checked(대조 개수), matched(일치 개수), mismatches(location·
    expected·actual)를 담는다. 실패하면 error와 실패한 단계(step)를 담는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        result = _verify(document, source, group_by, value)
        result["summary"] = f"{result['checked']}개 중 {result['matched']}개 일치"
    except ReportError as exc:
        result = {"error": exc.message, "step": exc.step}
    except Exception as exc:  # engine or file failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected"}
    result["run_id"] = run_id
    return json.dumps(result, ensure_ascii=False)
