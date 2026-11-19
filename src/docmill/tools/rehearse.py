"""`register_reviewer` / `rehearse_review` 업무 도구 (FR-43).

예전 결재 코멘트에서 사람별 기준(규칙)을 뽑아 `DOCMILL_HOME/reviewers/<이름>.json`에 저장하고,
올리기 전에 그 기준으로 문서를 검사해 걸리는 곳을 돌려준다. 규칙은 세 가지다.

- `길이`: '너무 길다'·'한 장으로 줄여' 류 코멘트 → 장 수 상한(limit). 숫자가 없으면 기본값
- `근거`: '근거가 뭐야'·'출처' 류 코멘트 → review_map(FR-33)의 `출처 없음` 결과를 가져온다
- `결론 위치`: '결론이 뒤에' 류 코멘트 → 결론 문장이 문서 앞쪽에 없으면 알린다

규칙 기반이며(AI·시각·난수 없음) 같은 입력이면 같은 결과를 돌려준다.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

from docmill.runlog import current_run_id, get_home, new_run_id
from docmill.tools.provenance import slide_parts
from docmill.tools.review import (
    UNSOURCED,
    ReviewError,
    Segment,
    build_review,
    read_segments,
)

LENGTH = "길이"
EVIDENCE = "근거"
CONCLUSION = "결론 위치"
TOO_LONG = "길이 초과"
DEFAULT_LIMIT = 3
CHARS_PER_PAGE = 1500
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_KOREAN_COUNTS = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5}
_LIMIT = re.compile(r"(\d+|한|두|세|네|다섯)\s*(?:장|쪽|페이지|슬라이드|p\b)")
_LONG_WORDS = ("너무 길", "길다", "길어", "줄여", "분량", "간결", "짧게")
_EVIDENCE_WORDS = ("근거", "출처", "어디서", "왜 이")
_CONCLUSION_WORDS = ("결론", "두괄", "요점", "핵심")
_CONCLUSION_HINTS = ("뒤", "먼저", "앞", "위로", "두괄", "마지막")
_CONCLUSION_MARKS = ("결론", "요약", "따라서", "정리하면")
_FRONT_RATIO = 1 / 3


class ReviewerError(Exception):
    """기준을 등록·불러오지 못했을 때 실패한 단계(step)와 이유를 담는다."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


def _store(name: str) -> Path:
    return get_home() / "reviewers" / f"{quote(name, safe='')}.json"


# ── 코멘트 읽기 ──────────────────────────────────────────────────────────


def _docx_comments(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/comments.xml"))
    except KeyError:
        return []
    except (zipfile.BadZipFile, ET.ParseError, OSError) as exc:
        raise ReviewerError("read", f"코멘트 파일을 읽지 못했습니다: {exc}") from exc
    comments = []
    for comment in root.iter(f"{_W}comment"):
        text = "".join(t.text or "" for t in comment.iter(f"{_W}t")).strip()
        if text:
            comments.append(text)
    return comments


def _file_comments(raw: str) -> list[str]:
    path = Path(raw)
    if not path.is_file():
        raise ReviewerError("read", f"코멘트 파일을 찾을 수 없습니다: {raw}")
    if path.suffix.lower() == ".docx":
        return _docx_comments(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ReviewerError("read", f"코멘트 파일을 읽지 못했습니다: {exc}") from exc
    return [line.strip() for line in lines if line.strip()]


# ── 규칙 뽑기 ────────────────────────────────────────────────────────────


def _length_limit(comments: list[str]) -> int:
    """코멘트에 적힌 장 수 중 가장 작은 값. 없으면 기본값."""
    found: list[int] = []
    for comment in comments:
        for m in _LIMIT.finditer(comment):
            word = m.group(1)
            found.append(int(word) if word.isdigit() else _KOREAN_COUNTS[word])
    return max(1, min(found)) if found else DEFAULT_LIMIT


def derive_rules(comments: list[str]) -> list[dict[str, Any]]:
    """코멘트 목록에서 규칙을 뽑는다. 종류 순서는 길이·근거·결론 위치로 고정이다."""
    rules: list[dict[str, Any]] = []
    long_ = [c for c in comments if any(w in c for w in _LONG_WORDS)]
    if long_:
        rules.append({"kind": LENGTH, "limit": _length_limit(long_), "source": long_[0]})
    evidence = [c for c in comments if any(w in c for w in _EVIDENCE_WORDS)]
    if evidence:
        rules.append({"kind": EVIDENCE, "source": evidence[0]})
    conclusion = [
        c
        for c in comments
        if any(w in c for w in _CONCLUSION_WORDS) and any(h in c for h in _CONCLUSION_HINTS)
    ]
    if conclusion:
        rules.append({"kind": CONCLUSION, "source": conclusion[0]})
    return rules


# ── 검사 ────────────────────────────────────────────────────────────────


def _docx_pages(document: Path) -> int | None:
    try:
        with zipfile.ZipFile(document) as archive:
            root = ET.fromstring(archive.read("docProps/app.xml"))
    except (KeyError, zipfile.BadZipFile, ET.ParseError, OSError):
        return None
    for element in root.iter():
        if element.tag.endswith("}Pages") and (element.text or "").strip().isdigit():
            return int(str(element.text).strip())
    return None


def measure_size(document: Path, segments: list[Segment]) -> int:
    """문서 분량(장 수). pptx는 슬라이드 수, docx는 쪽 수, 없으면 글자 수로 어림한다."""
    suffix = document.suffix.lower()
    if suffix == ".pptx":
        return _slide_count(document) or 1
    pages = _docx_pages(document) if suffix == ".docx" else None
    if pages:
        return pages
    chars = sum(len(s.text) for s in segments)
    estimate = max(1, math.ceil(chars / CHARS_PER_PAGE))
    if suffix == ".docx":
        return max(estimate, _docx_page_breaks(document) + 1)
    return estimate


def _docx_page_breaks(document: Path) -> int:
    """word/document.xml의 쪽 나누기(`w:br w:type="page"`) 개수."""
    try:
        with zipfile.ZipFile(document) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
    except (KeyError, zipfile.BadZipFile, ET.ParseError, OSError):
        return 0
    return sum(1 for br in root.iter(f"{_W}br") if br.get(f"{_W}type") == "page")


def _slide_count(document: Path) -> int | None:
    if document.suffix.lower() != ".pptx":
        return None
    try:
        with zipfile.ZipFile(document) as archive:
            return len(slide_parts(archive))
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError):
        return None


def _length_items(rule: dict[str, Any], document: Path, segments: list[Segment]) -> list[Any]:
    limit = int(rule["limit"])
    current = measure_size(document, segments)
    if current <= limit:
        return []
    text = f"현재 {current}장, 기준 {limit}장"
    return [{"reason": TOO_LONG, "text": text, "location": {}, "current": current, "limit": limit}]


def _conclusion_items(segments: list[Segment]) -> list[dict[str, Any]]:
    texts = [s for s in segments if s.kind == "text"]
    for index, segment in enumerate(texts):
        if any(mark in segment.text for mark in _CONCLUSION_MARKS):
            if index < len(texts) * _FRONT_RATIO:
                return []
            reason = "결론이 뒤에 있음"
            return [{"reason": reason, "text": segment.text, "location": segment.location}]
    if not texts:
        return []
    return [{"reason": "결론을 찾지 못함", "text": "결론 문장이 없어요", "location": {}}]


def _evidence_items(document: Path) -> list[dict[str, Any]]:
    items = build_review(document).get("items", [])
    return [item for item in items if item["reason"] == UNSOURCED]


def _rehearse(rules: list[dict[str, Any]], document: Path) -> list[dict[str, Any]]:
    segments = read_segments(document)
    items: list[dict[str, Any]] = []
    for rule in rules:
        kind = rule.get("kind")
        if kind == LENGTH:
            items += _length_items(rule, document, segments)
        elif kind == EVIDENCE:
            items += _evidence_items(document)
        elif kind == CONCLUSION:
            items += _conclusion_items(segments)
    return items


# ── 도구 ────────────────────────────────────────────────────────────────


def _dump(result: dict[str, Any], run_id: str) -> str:
    result["run_id"] = run_id
    return json.dumps(result, ensure_ascii=False)


def register_reviewer(
    name: str, comments: list[str] | None = None, comments_file: str | None = None
) -> str:
    """예전 결재 코멘트에서 사람별 기준을 뽑아 저장한다 (규칙 기반, AI 없음).

    name은 결재자 이름, comments는 코멘트 문자열 목록, comments_file은 docx 메모(코멘트)
    또는 한 줄에 하나씩 적은 텍스트 파일 경로다. 결과 JSON의 rules에 종류(kind: '길이'·'근거'·
    '결론 위치')와 길이 규칙의 limit(장 수)이 담긴다. 기준은 DOCMILL_HOME에 남아 다음 세션에서도
    쓰인다. 같은 이름으로 다시 등록하면 기준이 바뀐다. 실패하면 error와 실패한 단계(step)를 담는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        who = name.strip()
        if not who:
            raise ReviewerError("input", "결재자 이름이 비어 있습니다.")
        collected = [c.strip() for c in comments or [] if c.strip()]
        if comments_file:
            collected += _file_comments(comments_file)
        rules = derive_rules(collected)
        path = _store(who)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"reviewer": who, "rules": rules}
        _write_atomic(path, json.dumps(record, ensure_ascii=False, indent=2))
        result: dict[str, Any] = {**record, "comment_count": len(collected)}
        if not rules:
            result["message"] = "코멘트에서 뽑을 기준을 찾지 못했어요."
    except ReviewerError as exc:
        result = {"error": exc.message, "step": exc.step}
    except Exception as exc:  # failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected"}
    return _dump(result, run_id)


def _write_atomic(path: Path, text: str) -> None:
    """호출마다 고유한 임시 파일에 완성된 내용을 쓴 뒤 교체한다. 실패하면 기존 파일을 유지한다."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _load_rules(name: str) -> list[dict[str, Any]] | None:
    path = _store(name.strip())
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("rules", []))


def rehearse_review(reviewer: str, document: str) -> str:
    """올리기 전에 등록된 결재자 기준으로 문서를 검사해 걸리는 곳을 돌려준다 (규칙 기반).

    reviewer는 register_reviewer로 등록한 이름, document는 pptx·docx 경로다. 결과 JSON의
    items에 reason('길이 초과'·'출처 없음'·'결론이 뒤에 있음'·'결론을 찾지 못함'), text, location이
    담기고 길이 초과는 current(현재 장 수)와 limit(기준)도 담긴다. 등록하지 않은 이름이면 오류 대신
    message에 '기준이 없어요'를 돌려준다. 실패하면 error와 실패한 단계(step)를 담는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        rules = _load_rules(reviewer)
        if rules is None:
            result: dict[str, Any] = {
                "reviewer": reviewer,
                "items": [],
                "message": f"{reviewer}님의 기준이 없어요. register_reviewer로 먼저 등록해 주세요.",
            }
        else:
            items = _rehearse(rules, Path(document))
            result = {
                "reviewer": reviewer,
                "items": items,
                "summary": f"걸리는 곳 {len(items)}곳",
            }
    except ReviewError as exc:
        result = {"error": exc.message, "step": exc.step}
    except Exception as exc:  # document failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected"}
    return _dump(result, run_id)
