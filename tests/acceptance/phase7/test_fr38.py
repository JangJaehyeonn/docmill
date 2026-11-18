"""FR-38 공문서 작성 규칙 검사·교정 (check_official_style) 인수 테스트 (초안).

필요한 샘플: tests/acceptance_draft/phase7/FIXTURES.md 의 `official_style.hwp`
(docx 테스트와 같은 문단을 담은 한글 파일). docx는 테스트가 직접 만든다.
가정(구현 방법이 정해지면 맞춰 고친다):
- MCP 도구 `check_official_style` 인자: `document`(docx·hwp·hwpx), 선택 `fix`(기본 false).
- 결과 JSON: `violations` 목록. 각 항목은 `location`, `rule`, `before`, `after`(고칠 수 없으면 없음).
  rule 이름: `날짜 표기`·`시간 표기`·`항목 기호`·`금액 표기`·`끝 표시`.
  `fix`를 켜면 결과 `path`에 고친 문서가 생기고 원본은 그대로다.
"""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
HWP = FIXTURES / "official_style.hwp"

_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)

_FR = "FR-38"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)

# official_style.hwp도 같은 문단이어야 한다.
PARAGRAPHS = [
    "행사 개최 안내",
    "1. 일시: 2026-11-16 14:30",
    "2. 장소: 본관 대회의실",
    "3. 예산: 10,150원",
    "1) 세부 일정은 별도 안내",
    "2026년 11월 17일에 다시 알린다.",
]
NO_END = "마감 확인 필요"


def _make_docx(path: Path, paragraphs: list[str]) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", doc)
    return path


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    return "\n".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))


async def _call(home: Path, name: str, args: dict[str, Any]) -> dict[str, Any]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "docmill.cli", "mcp"],
        env={**os.environ, "DOCMILL_HOME": str(home)},
        cwd=str(home),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(name, args)
    text = "".join(getattr(c, "text", "") for c in result.content)
    return dict(json.loads(text))


def _check(home: Path, doc: Path, fix: bool = False) -> dict[str, Any]:
    args: dict[str, Any] = {"document": str(doc), "fix": fix}
    return asyncio.run(asyncio.wait_for(_call(home, "check_official_style", args), 120))


def _by_rule(out: dict[str, Any], rule: str) -> list[dict[str, Any]]:
    return [v for v in out["violations"] if v["rule"] == rule]


@pytest.mark.fr("FR-38")
def test_dates_are_fixed_to_official_format_with_locations(tmp_path: Path) -> None:
    doc = _make_docx(tmp_path / "a.docx", PARAGRAPHS)
    out = _check(tmp_path, doc, fix=True)
    dates = _by_rule(out, "날짜 표기")
    assert len(dates) == 2, dates
    assert all(v["location"] for v in dates)
    assert {v["after"] for v in dates} == {"2026. 11. 16.", "2026. 11. 17."}
    text = _docx_text(Path(out["path"]))
    assert "2026. 11. 16." in text and "2026. 11. 17." in text
    assert "2026-11-16" not in text and "2026년 11월 17일" not in text


@pytest.mark.fr("FR-38")
def test_broken_bullet_order_is_reported(tmp_path: Path) -> None:
    doc = _make_docx(tmp_path / "b.docx", PARAGRAPHS)
    items = _by_rule(_check(tmp_path, doc), "항목 기호")
    assert items, "1. 아래 바로 1)은 위반이다"
    assert any("1)" in json.dumps(v, ensure_ascii=False) for v in items)
    assert all(v["location"] for v in items)


@pytest.mark.fr("FR-38")
def test_amount_is_fixed_with_korean_numerals(tmp_path: Path) -> None:
    doc = _make_docx(tmp_path / "c.docx", PARAGRAPHS)
    out = _check(tmp_path, doc, fix=True)
    amounts = _by_rule(out, "금액 표기")
    assert [v["after"] for v in amounts] == ["금10,150원(금일만백오십원)"]
    assert "금10,150원(금일만백오십원)" in _docx_text(Path(out["path"]))


@pytest.mark.fr("FR-38")
def test_missing_end_mark_is_reported_and_added_with_fix(tmp_path: Path) -> None:
    doc = _make_docx(tmp_path / "d.docx", [*PARAGRAPHS, NO_END])
    assert _by_rule(_check(tmp_path, doc), "끝 표시"), "끝.이 없으면 위반이다"
    out = _check(tmp_path, doc, fix=True)
    assert _docx_text(Path(out["path"])).rstrip().endswith("끝.")
    done = _make_docx(tmp_path / "e.docx", [*PARAGRAPHS, f"{NO_END}.  끝."])
    assert _by_rule(_check(tmp_path, done), "끝 표시") == []


@pytest.mark.fr("FR-38")
def test_hwp_and_docx_give_same_result(tmp_path: Path) -> None:
    docx = _make_docx(tmp_path / "same.docx", PARAGRAPHS)
    a = _check(tmp_path, docx)["violations"]
    b = _check(tmp_path, HWP)["violations"]
    assert a, "위반이 있어야 비교가 된다"

    def key(vs: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
        return sorted((v["rule"], v.get("after")) for v in vs)

    assert key(a) == key(b)
