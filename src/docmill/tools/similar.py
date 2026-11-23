"""비슷한 문서 찾기 (FR-48).

작업 폴더에서 입력한 작업 이름과 제목이 비슷한 예전 문서를 골라 가까운 순서로 돌려준다.
규칙·글자 유사도만 쓰며(AI·엔진·시각·난수 없음) 같은 폴더와 같은 입력이면 같은 순서를 낸다.
"""

from __future__ import annotations

import html
import json
import re
import zipfile
import zlib
from pathlib import Path
from typing import Any

from docmill.tools.compare import read_snapshot
from docmill.tools.hwp import HwpError, is_hwp, read_hwp_lines
from docmill.tools.review import ReviewError

MAX_SUGGESTIONS = 5
MIN_SCORE = 0.5
MAX_XML_BYTES = 20 * 1024 * 1024
_EXTENSIONS = {".xlsx", ".pptx", ".docx", ".hwp", ".hwpx"}
_SKIP_PREFIXES = (".", "~$")
_TEXT_RUN = re.compile(r"<(?:w|a):t(?:\s[^>]*)?>([^<]*)")
_CORE_TITLE = re.compile(r"<dc:title(?:\s[^>]*)?>([^<]*)</dc:title>")


def _normalize(text: str) -> str:
    """공백·기호를 없애고 소문자로 바꾼다."""
    return re.sub(r"[\W_]+", "", text.lower())


def _bigrams(text: str) -> set[str]:
    return {text[i : i + 2] for i in range(len(text) - 1)} or ({text} if text else set())


def similarity(query: str, title: str) -> float:
    """두 글의 유사도(0~1). 한쪽이 다른 쪽을 포함하면 1, 아니면 글자 2-gram Dice 계수."""
    a, b = _normalize(query), _normalize(title)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    ga, gb = _bigrams(a), _bigrams(b)
    return 2 * len(ga & gb) / (len(ga) + len(gb))


def _read_member(path: Path, name: str) -> str:
    try:
        with zipfile.ZipFile(path) as archive, archive.open(name) as member:
            raw = member.read(MAX_XML_BYTES + 1)
    except (OSError, KeyError, EOFError, zipfile.BadZipFile, zlib.error):
        return ""
    return "" if len(raw) > MAX_XML_BYTES else raw.decode("utf-8", "replace")


def _first_text(xml: str, paragraph_end: str) -> str:
    for chunk in xml.split(paragraph_end):
        text = html.unescape("".join(_TEXT_RUN.findall(chunk))).strip()
        if text:
            return text
    return ""


def _xlsx_title(path: Path) -> str:
    """문서 속성 title, 없으면 첫 시트(비어 있으면 다음 시트)의 첫 비어 있지 않은 셀."""
    match = _CORE_TITLE.search(_read_member(path, "docProps/core.xml"))
    title = html.unescape(match.group(1)).strip() if match else ""
    if title:
        return title
    try:
        cells = read_snapshot(path).cells
    except (ReviewError, ValueError, SyntaxError, EOFError, zlib.error):
        return ""
    return next((v.strip() for v in cells.values() if v.strip()), "")


def _hwp_title(path: Path) -> str:
    try:
        lines = read_hwp_lines(path)
    except HwpError:
        return ""
    return next((line.strip() for line in lines if line.strip()), "")


def document_title(path: Path) -> str:
    """문서의 제목으로 볼 첫 글을 읽는다.

    docx 첫 문단, pptx 첫 슬라이드 첫 글, xlsx 문서 속성 title 또는 첫 시트의 첫 셀,
    hwp·hwpx 첫 문단. 읽지 못하면 빈 문자열."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _first_text(_read_member(path, "word/document.xml"), "</w:p>")
    if suffix == ".pptx":
        return _first_text(_read_member(path, "ppt/slides/slide1.xml"), "</a:p>")
    if suffix == ".xlsx":
        return _xlsx_title(path)
    if is_hwp(path):
        return _hwp_title(path)
    return ""


def _documents(root: Path) -> list[Path]:
    found: list[Path] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part.startswith(_SKIP_PREFIXES) or part == "node_modules" for part in rel.parts):
            continue
        if p.is_file() and not p.is_symlink() and p.suffix.lower() in _EXTENSIONS:
            found.append(p)
    return found


def find_similar_documents(workspace: str, query: str) -> str:
    """작업 폴더에서 `query`와 비슷한 문서를 가까운 순서로 JSON 문자열로 돌려준다.

    결과 `similar`의 각 항목은 path(작업 폴더 기준), title, score이다. 순서는 유사도 내림차순,
    같으면 수정 시각이 최근인 것, 그래도 같으면 경로 순이다.
    """
    root = Path(workspace).resolve()
    scored: list[tuple[float, float, str, str]] = []
    for p in _documents(root):
        title = document_title(p)
        score = max(similarity(query, title), similarity(query, p.stem))
        if score >= MIN_SCORE:
            scored.append((score, p.stat().st_mtime, p.relative_to(root).as_posix(), title))
    scored.sort(key=lambda s: (-s[0], -s[1], s[2]))
    items: list[dict[str, Any]] = [
        {"path": rel, "title": title, "score": round(score, 3)}
        for score, _m, rel, title in scored[:MAX_SUGGESTIONS]
    ]
    return json.dumps({"query": query, "similar": items}, ensure_ascii=False)
