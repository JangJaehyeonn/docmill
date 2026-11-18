"""`preflight` 업무 도구 (FR-34).

문서를 보내기 전에 숫자 검증(원본이 있으면), 날짜와 요일 일치, 개인정보 노출, 남은 자리표시자,
빈 제목을 한 번에 점검해 항목별 상태(통과·주의·실패)와 위치를 돌려준다.

규칙 기반이며(AI·엔진·시각·난수 없음) 같은 입력이면 같은 결과를 돌려준다. 개인정보는 결과에
원문을 쓰지 않고 앞 두 글자만 남기고 가린다. 지원 형식은 pptx와 docx다.
"""

from __future__ import annotations

import datetime
import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from docmill.runlog import current_run_id, new_run_id
from docmill.tools.provenance import slide_parts
from docmill.tools.review import (
    _A,
    _P,
    _W,
    ReviewError,
    Segment,
    _paragraph_text,
    read_segments,
)

PASS = "통과"
WARN = "주의"
FAIL = "실패"

NUMBERS = "숫자 검증"
WEEKDAY = "날짜 요일"
PRIVACY = "개인정보"
PLACEHOLDER = "자리표시자"
EMPTY_TITLE = "빈 제목"

_WEEKDAYS = "월화수목금토일"
_DATE = re.compile(
    r"(?P<y>\d{4})\s*[.\-/년]\s*(?P<m>\d{1,2})\s*[.\-/월]\s*(?P<d>\d{1,2})\s*[.일]?"
    r"\s*(?:\(\s*(?P<w1>[월화수목금토일])(?:요일)?\s*\)|(?P<w2>[월화수목금토일])요일)"
)
_RRN = re.compile(r"(?<!\d)\d{6}-[1-4]\d{6}(?!\d)")
_PHONE = re.compile(r"(?<![\d-])0\d{1,2}[-. ]?\d{3,4}[-. ]?\d{4}(?![\d-])")
_ACCOUNT = re.compile(r"(?<![\d-])\d{2,6}(?:-\d{2,8}){2,3}(?![\d-])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PRIVACY_RULES = (
    ("주민등록번호", _RRN),
    ("전화번호", _PHONE),
    ("이메일", _EMAIL),
    ("계좌번호", _ACCOUNT),
)
_PLACEHOLDER = re.compile(
    r"(?<![A-Za-z])(?:TBD|TBA)(?![A-Za-z])|\[[^\]\n]*(?:작성|입력)[^\]\n]*\]|[○◯□]{2,}|XXX+"
)
_TITLE_STYLE = re.compile(r"(?:heading\s*\d*|title|제목\s*\d*)", re.IGNORECASE)
_TITLE_PLACEHOLDERS = {"title", "ctrTitle"}


def _entry(location: dict[str, Any], text: str, **extra: Any) -> dict[str, Any]:
    return {"location": location, "text": text, **extra}


def _status(locations: list[dict[str, Any]], bad: str) -> str:
    return bad if locations else PASS


def _check(name: str, status: str, locations: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": name, "status": status, "locations": locations}


# ── 규칙 ────────────────────────────────────────────────────────────────


def _weekday_problem(match: re.Match[str]) -> str | None:
    """날짜와 요일이 안 맞거나 없는 날짜면 이유를, 맞으면 None을 돌려준다."""
    try:
        day = datetime.date(int(match["y"]), int(match["m"]), int(match["d"]))
    except ValueError:
        return "없는 날짜"
    written = match["w1"] or match["w2"]
    actual = _WEEKDAYS[day.weekday()]
    return None if written == actual else f"{actual}요일이어야 함"


def check_weekdays(segments: list[Segment]) -> dict[str, Any]:
    """'2026. 11. 16.(화)'처럼 날짜와 요일이 어긋난 곳을 찾는다."""
    found: list[dict[str, Any]] = []
    for segment in segments:
        for match in _DATE.finditer(segment.text):
            problem = _weekday_problem(match)
            if problem:
                found.append(_entry(segment.location, match.group(0), reason=problem))
    return _check(WEEKDAY, _status(found, FAIL), found)


def mask(raw: str) -> str:
    """앞 두 글자만 남기고 나머지 글자·숫자는 `*`로 가린다."""
    kept = 0
    out: list[str] = []
    for ch in raw:
        if ch.isalnum() and kept < 2:
            kept += 1
            out.append(ch)
        elif ch.isalnum():
            out.append("*")
        else:
            out.append(ch)
    return "".join(out)


def _privacy_matches(text: str) -> list[tuple[str, str]]:
    """(종류, 원문) 목록. 먼저 잡힌 형식과 겹치는 부분은 다시 세지 않는다."""
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, str, str]] = []
    for kind, pattern in _PRIVACY_RULES:
        for m in pattern.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            if pattern is _ACCOUNT and sum(c.isdigit() for c in m.group(0)) < 10:
                continue  # dates like 2026-11-16 are not account numbers
            taken.append((m.start(), m.end()))
            found.append((m.start(), kind, m.group(0)))
    return [(kind, raw) for _, kind, raw in sorted(found)]


def mask_privacy(text: str) -> str:
    """문장 안의 개인정보 구간만 가려서 돌려준다 (다른 검사 결과에 원문이 새지 않게)."""
    for _, raw in _privacy_matches(text):
        text = text.replace(raw, mask(raw))
    return text


def check_privacy(segments: list[Segment]) -> dict[str, Any]:
    """주민등록번호·전화번호·계좌번호·이메일을 찾는다. 결과에는 가린 값만 쓴다."""
    found = [
        _entry(segment.location, mask(raw), kind=kind)
        for segment in segments
        for kind, raw in _privacy_matches(segment.text)
    ]
    return _check(PRIVACY, _status(found, FAIL), found)


def check_placeholders(segments: list[Segment]) -> dict[str, Any]:
    """TBD·[작성]·○○○ 같은 자리표시자가 남은 곳을 찾는다."""
    found = [
        _entry(segment.location, mask_privacy(match.group(0)))
        for segment in segments
        for match in _PLACEHOLDER.finditer(segment.text)
    ]
    return _check(PLACEHOLDER, _status(found, WARN), found)


def _docx_empty_titles(archive: zipfile.ZipFile) -> list[dict[str, Any]]:
    root = ET.fromstring(archive.read("word/document.xml"))
    found: list[dict[str, Any]] = []
    for p_no, paragraph in enumerate(root.iter(f"{_W}p"), start=1):
        style = paragraph.find(f"{_W}pPr/{_W}pStyle")
        name = style.get(f"{_W}val", "") if style is not None else ""
        if _TITLE_STYLE.fullmatch(name) and not _paragraph_text(paragraph, f"{_W}t"):
            found.append(_entry({"paragraph": p_no}, ""))
    return found


def _pptx_empty_titles(archive: zipfile.ZipFile) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for slide_no, part in enumerate(slide_parts(archive), start=1):
        root = ET.fromstring(archive.read(part))
        for shape in root.iter(f"{_P}sp"):
            ph = shape.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
            if ph is None or ph.get("type") not in _TITLE_PLACEHOLDERS:
                continue
            if any(_paragraph_text(p, f"{_A}t") for p in shape.iter(f"{_A}p")):
                continue
            c_nv = shape.find(f"{_P}nvSpPr/{_P}cNvPr")
            shape_id = str(c_nv.get("id", "")) if c_nv is not None else ""
            found.append(_entry({"slide": slide_no, "shape": shape_id}, ""))
    return found


def check_empty_titles(document: Path) -> dict[str, Any]:
    """제목 서식인데 글자가 없는 문단·제목 상자를 찾는다."""
    finder = _docx_empty_titles if document.suffix.lower() == ".docx" else _pptx_empty_titles
    try:
        with zipfile.ZipFile(document) as archive:
            found = finder(archive)
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError) as exc:
        raise ReviewError("read", f"문서를 읽지 못했습니다: {exc}") from exc
    return _check(EMPTY_TITLE, _status(found, WARN), found)


def check_numbers(document: Path, source: str | None, group_by: str, value: str) -> dict[str, Any]:
    """원본 엑셀이 있으면 verify_numbers로 그래프 숫자를 대조한다. 없으면 건너뛴다(통과)."""
    if not source:
        return {**_check(NUMBERS, PASS, []), "note": "원본이 없어 건너뜀"}
    from docmill.tools.report import ReportError
    from docmill.tools.verify import _verify

    try:
        result = _verify(str(document), source, group_by, value)
    except ReportError as exc:
        return {**_check(NUMBERS, FAIL, []), "error": exc.message, "step": exc.step}
    found = [_entry(m.get("location", {}), str(m.get("actual", ""))) for m in result["mismatches"]]
    return {**_check(NUMBERS, _status(found, FAIL), found), "checked": result["checked"]}


# ── 도구 ────────────────────────────────────────────────────────────────


def build_preflight(
    document: Path, source: str | None = None, group_by: str = "", value: str = ""
) -> dict[str, Any]:
    """점검 항목 목록(checks)을 만든다. 문서를 읽지 못하면 ReviewError를 낸다."""
    segments = [s for s in read_segments(document) if s.kind == "text"]
    checks = [
        check_numbers(document, source, group_by, value),
        check_weekdays(segments),
        check_privacy(segments),
        check_placeholders(segments),
        check_empty_titles(document),
    ]
    return {"checks": checks}


def _summary(checks: list[dict[str, Any]]) -> str:
    counts = [(s, sum(1 for c in checks if c["status"] == s)) for s in (FAIL, WARN, PASS)]
    return "보내기 전 점검: " + ", ".join(f"{s} {n}" for s, n in counts)


def preflight(document: str, source: str | None = None, group_by: str = "", value: str = "") -> str:
    """문서를 보내기 전에 안전 점검을 한 번에 한다 (규칙 기반, AI 없음).

    document는 pptx·docx 경로다. 원본 엑셀(source)과 group_by·value를 주면 그래프 숫자를 다시
    계산해 대조한다. 결과 JSON의 checks에 항목(name: 숫자 검증·날짜 요일·개인정보·자리표시자·
    빈 제목), status(통과·주의·실패), locations(위치와 해당 글자)가 담긴다. 개인정보는 원래
    값을 쓰지 않고 가려서 돌려준다. 실패하면 error와 실패한 단계(step)를 담는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        result = build_preflight(Path(document), source, group_by, value)
        result["summary"] = _summary(result["checks"])
    except ReviewError as exc:
        result = {"error": exc.message, "step": exc.step}
    except Exception as exc:  # document failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected"}
    result["run_id"] = run_id
    return json.dumps(result, ensure_ascii=False)
