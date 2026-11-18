"""`check_official_style` 업무 도구 (FR-38).

한글(hwp·hwpx)·워드(docx) 공문을 행정업무운영편람 규칙으로 검사하고, 고칠 수 있는 것은 고친다.
규칙: 날짜 표기(2026. 11. 16.), 시간 표기(14:30), 항목 기호 순서(1. → 가. → 1) → 가) → (1) → (가)),
금액 표기(금10,150원(금일만백오십원)), 본문 끝 '끝.' 표시.

규칙 기반이며(AI·엔진·시각·난수 없음) 같은 입력이면 같은 결과를 돌려준다.
고치기는 docx만 지원한다. 한글 문서는 읽기만 되므로 위반만 알려준다.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docmill.runlog import current_run_id, get_home, new_run_id, output_stem
from docmill.tools.hwp import is_hwp
from docmill.tools.ooxml import Package
from docmill.tools.review import ReviewError, Segment, read_segments

DATE = "날짜 표기"
TIME = "시간 표기"
BULLET = "항목 기호"
AMOUNT = "금액 표기"
END_MARK = "끝 표시"

END_TEXT = "끝."
_END_SEPARATOR = "  "
_END_PATTERN = re.compile(r"(?:^|\s)끝\.$")

_DATE_DASH = re.compile(r"(?<![\d.\-/])(\d{4})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})(?![\d\-/])")
_DATE_KOREAN = re.compile(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_DATE_DOT = re.compile(r"(?<![\d.\-/])(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})(?:\s*\.(?!\d))?")
_TIME_KOREAN = re.compile(r"(?<![\d:])(\d{1,2})\s*시(?!간)(?:\s*(\d{1,2})\s*분)?")
_TIME_AMPM = re.compile(r"(오전|오후)\s*(\d{1,2})\s*시(?!간)(?:\s*(\d{1,2})\s*분)?")
# "3원칙", "100원짜리" 같은 낱말은 금액이 아니다.
_AMOUNT = re.compile(
    r"(?<![\d,.])(?:금\s*)?(\d{1,3}(?:,\d{3})+|\d+)\s*원(?!칙|래|본|인|장|리|천|룡|형|판)"
    r"(?:\s*\(\s*금[^)\n]*원\s*\))?"
)
_DIGITS = "영일이삼사오육칠팔구"
_SMALL_UNITS = ("", "십", "백", "천")
_BIG_UNITS = ("", "만", "억", "조", "경")
_HANGUL_ORDER = "가나다라마바사아자차카타파하"
_BULLET_PATTERNS = (
    re.compile(r"(\d{1,2})\.(?:\s|$)"),
    re.compile(r"([가-하])\.(?:\s|$)"),
    re.compile(r"(\d{1,2})\)(?:\s|$)"),
    re.compile(r"([가-하])\)(?:\s|$)"),
    re.compile(r"\((\d{1,2})\)(?:\s|$)"),
    re.compile(r"\(([가-하])\)(?:\s|$)"),
)
_BULLET_NAMES = ("1.", "가.", "1)", "가)", "(1)", "(가)")
_BULLET_CLOSERS = (1, 1, 1, 1, 2, 2)


class StyleError(Exception):
    """공문서 검사를 하지 못했을 때 실패한 단계(step)와 이유를 담는다."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


@dataclass(frozen=True)
class Edit:
    """글자 구간 [start, end)를 after로 바꾸는 교정 하나."""

    start: int
    end: int
    rule: str
    after: str


# ── 규칙: 글자를 바꿔서 고칠 수 있는 것 ──────────────────────────────────


def korean_numeral(value: int) -> str:
    """0 이상의 정수를 공문 금액 한글 표기로 바꾼다 (10150 → 일만백오십)."""
    if value == 0:
        return _DIGITS[0]
    groups: list[int] = []
    while value:
        groups.append(value % 10000)
        value //= 10000
    parts: list[str] = []
    for index in reversed(range(len(groups))):
        if groups[index]:
            parts.append(_group_numeral(groups[index]) + _BIG_UNITS[index])
    return "".join(parts)


def _group_numeral(group: int) -> str:
    """네 자리 이하 묶음. 십·백·천 앞의 '일'은 생략하고, 묶음이 1이면 '일'을 쓴다."""
    if group == 1:
        return _DIGITS[1]
    out: list[str] = []
    for place in range(3, -1, -1):
        digit = group // 10**place % 10
        if digit == 0:
            continue
        if place == 0:
            out.append(_DIGITS[digit])
        else:
            out.append((_DIGITS[digit] if digit > 1 else "") + _SMALL_UNITS[place])
    return "".join(out)


def _date_edits(text: str) -> list[Edit]:
    found: list[Edit] = []
    for pattern in (_DATE_DASH, _DATE_KOREAN, _DATE_DOT):
        for m in pattern.finditer(text):
            year, month, day = (int(g) for g in m.groups())
            if year < 1 or not 1 <= month <= 12 or not 1 <= day <= 31:
                continue
            after = f"{year}. {month}. {day}."
            if m.group(0) != after:
                found.append(Edit(m.start(), m.end(), DATE, after))
    return found


def _clock(hour: int, minute: int) -> str | None:
    if hour > 24 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _time_edits(text: str) -> list[Edit]:
    found: list[Edit] = []
    for m in _TIME_AMPM.finditer(text):
        raw_hour = int(m.group(2))
        hour = raw_hour % 12 + (12 if m.group(1) == "오후" else 0)
        after = _clock(hour, int(m.group(3) or 0)) if raw_hour <= 12 else None
        if after:
            found.append(Edit(m.start(), m.end(), TIME, after))
    for m in _TIME_KOREAN.finditer(text):
        after = _clock(int(m.group(1)), int(m.group(2) or 0))
        if after:
            found.append(Edit(m.start(), m.end(), TIME, after))
    return found


def _amount_edits(text: str) -> list[Edit]:
    found: list[Edit] = []
    for m in _AMOUNT.finditer(text):
        number = int(m.group(1).replace(",", ""))
        after = f"금{number:,}원(금{korean_numeral(number)}원)"
        if m.group(0) != after:
            found.append(Edit(m.start(), m.end(), AMOUNT, after))
    return found


def text_edits(text: str) -> list[Edit]:
    """글자 규칙(날짜·시간·금액)에 어긋난 구간과 고친 값. 겹치면 먼저 잡힌 것만 남긴다."""
    ordered = sorted(
        _date_edits(text) + _time_edits(text) + _amount_edits(text),
        key=lambda e: (e.start, e.end, e.rule),
    )
    kept: list[Edit] = []
    for edit in ordered:
        if kept and edit.start < kept[-1].end:
            continue
        kept.append(edit)
    return kept


def apply_edits(text: str, edits: list[Edit]) -> str:
    """겹치지 않는 교정을 글자에 적용한다."""
    for edit in sorted(edits, key=lambda e: e.start, reverse=True):
        text = text[: edit.start] + edit.after + text[edit.end :]
    return text


# ── 규칙: 알리기만 하는 것 ───────────────────────────────────────────────


def _bullet_of(text: str) -> tuple[int, str] | None:
    """문단 앞의 항목 기호 (단계, 기호 글자). 기호가 없으면 None."""
    for level, pattern in enumerate(_BULLET_PATTERNS):
        m = pattern.match(text)
        if m is None or (level in (1, 3, 5) and m.group(1) not in _HANGUL_ORDER):
            continue
        return level, text[: m.end(1) + _BULLET_CLOSERS[level]]
    return None


def bullet_violations(segments: list[Segment]) -> list[dict[str, Any]]:
    """항목 기호가 1. → 가. → 1) → 가) → (1) → (가) 순서를 건너뛴 문단을 찾는다."""
    found: list[dict[str, Any]] = []
    previous = -1
    for segment in segments:
        bullet = _bullet_of(segment.text)
        if bullet is None:
            continue
        level, marker = bullet
        if level > previous + 1:
            above = _BULLET_NAMES[previous] if previous >= 0 else "항목 기호 없음"
            reason = f"{above} 아래에는 {_BULLET_NAMES[previous + 1]} 순서인데 {marker}를 썼다"
            found.append(_violation(BULLET, segment.location, marker, None, reason=reason))
        previous = level
    return found


def end_mark_violation(segments: list[Segment]) -> dict[str, Any] | None:
    """마지막 문단이 '끝.'으로 끝나지 않으면 위반을 돌려준다."""
    if not segments or _END_PATTERN.search(segments[-1].text):
        return None
    last = segments[-1]
    after = f"{last.text}{_END_SEPARATOR}{END_TEXT}"
    return _violation(END_MARK, last.location, "", after, reason="본문 끝에 '끝.'이 없다")


def _violation(
    rule: str, location: dict[str, Any], before: str, after: str | None, **extra: Any
) -> dict[str, Any]:
    item: dict[str, Any] = {"rule": rule, "location": location, "before": before}
    if after is not None:
        item["after"] = after
    return {**item, **extra}


def find_violations(segments: list[Segment]) -> list[dict[str, Any]]:
    """모든 규칙의 위반을 문서 순서(문단 → 문단 안 위치)로 돌려준다."""
    found: list[tuple[int, int, dict[str, Any]]] = []
    for segment in segments:
        paragraph = int(segment.location.get("paragraph", 0))
        for edit in text_edits(segment.text):
            location = {**segment.location, "offset": edit.start}
            before = segment.text[edit.start : edit.end]
            item = _violation(edit.rule, location, before, edit.after)
            found.append((paragraph, edit.start, item))
    for item in bullet_violations(segments):
        found.append((int(item["location"].get("paragraph", 0)), 0, item))
    end = end_mark_violation(segments)
    if end is not None:
        found.append((int(end["location"].get("paragraph", 0)), 10**9, end))
    found.sort(key=lambda t: (t[0], t[1], t[2]["rule"]))
    return [item for _, _, item in found]


# ── docx 고치기 ─────────────────────────────────────────────────────────

_W_T = re.compile(r"(<w:t(?:\s[^>]*)?>)([^<]*)(</w:t>)")


def _preserve_space(open_tag: str) -> str:
    if "xml:space" in open_tag:
        return open_tag
    return open_tag[:-1] + ' xml:space="preserve">'


def fix_docx_xml(xml: str, add_end_mark: bool) -> str:
    """document.xml의 글자 조각(w:t)마다 날짜·시간·금액을 고치고, 필요하면 '끝.'을 붙인다."""
    matches = list(_W_T.finditer(xml))
    originals = [html.unescape(m.group(2)) for m in matches]
    fixed = [apply_edits(t, text_edits(t)) for t in originals]
    last = max((i for i, t in enumerate(fixed) if t.strip()), default=None)
    out: list[str] = []
    cursor = 0
    for index, m in enumerate(matches):
        text = fixed[index]
        if add_end_mark and index == last:
            text = text.rstrip() + _END_SEPARATOR + END_TEXT
        open_tag = _preserve_space(m.group(1)) if text != originals[index] else m.group(1)
        out.append(xml[cursor : m.start()])
        out.append(f"{open_tag}{html.escape(text, quote=False)}{m.group(3)}")
        cursor = m.end()
    out.append(xml[cursor:])
    return "".join(out)


def _write_fixed(document: Path, add_end_mark: bool, out_dir: str | None, run_id: str) -> Path:
    try:
        package = Package.read(document.read_bytes())
        xml = package.parts["word/document.xml"].decode("utf-8")
    except (OSError, KeyError, UnicodeDecodeError, ValueError) as exc:
        raise StyleError("fix", f"문서를 고치지 못했습니다: {exc}") from exc
    data = fix_docx_xml(xml, add_end_mark).encode("utf-8")
    parts = {**package.parts, "word/document.xml": data}
    target_dir = Path(out_dir) if out_dir else get_home() / "outputs"
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / f"{output_stem(run_id)}{document.suffix.lower()}"
    final.write_bytes(package.write(parts))
    return final


# ── 도구 ────────────────────────────────────────────────────────────────


def build_check(document: Path, fix: bool, out_dir: str | None, run_id: str) -> dict[str, Any]:
    """검사 결과(violations 등)를 만든다. 읽지 못하면 ReviewError·StyleError를 낸다."""
    if document.suffix.lower() not in (".docx", ".hwp", ".hwpx"):
        raise ReviewError("open", f"지원하지 않는 형식입니다(docx·hwp·hwpx만): {document}")
    segments = [s for s in read_segments(document) if s.kind == "text"]
    violations = find_violations(segments)
    result: dict[str, Any] = {"violations": violations}
    if fix and is_hwp(document):
        result["note"] = "한글 문서는 고치기를 지원하지 않아 위반만 알려준다"
    elif fix:
        add_end = any(v["rule"] == END_MARK for v in violations)
        result["path"] = str(_write_fixed(document, add_end, out_dir, run_id))
        result["fixed"] = sum(1 for v in violations if "after" in v)
    result["summary"] = f"공문서 규칙 위반 {len(violations)}건"
    return result


def check_official_style(document: str, fix: bool = False, out_dir: str | None = None) -> str:
    """공문 작성 규칙(행정업무운영편람)을 검사하고 필요하면 고친다 (규칙 기반, AI 없음).

    document는 docx·hwp·hwpx 경로다. 검사하는 규칙은 날짜 표기(2026. 11. 16.), 시간 표기(14:30),
    항목 기호 순서(1. → 가. → 1) → 가) → (1) → (가)), 금액 표기(금10,150원(금일만백오십원)),
    본문 끝 '끝.' 표시다. 결과 JSON의 violations에 위치(location)·규칙(rule)·원래 글자(before)·
    고친 값(after, 고칠 수 없으면 없음)이 담긴다. fix를 켜면 고친 docx를 path에 저장하고 원본은
    그대로 둔다(out_dir을 주지 않으면 DOCMILL_HOME/outputs). 한글 문서는 고치기를 지원하지 않는다.
    실패하면 error와 실패한 단계(step)를 담는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        result = build_check(Path(document), fix, out_dir, run_id)
    except (ReviewError, StyleError) as exc:
        result = {"error": exc.message, "step": exc.step}
    except Exception as exc:  # document failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected"}
    result["run_id"] = run_id
    return json.dumps(result, ensure_ascii=False)
