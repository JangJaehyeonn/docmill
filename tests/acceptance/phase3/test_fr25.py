"""FR-25 워드 양식 적용 인수 테스트 (초안).

필요한 샘플 (아직 없음):
- tests/fixtures/template_word.docx: 회사 양식 워드. 제목 1~3에 크기·색·정렬·글꼴이 지정돼 있다.
- tests/fixtures/report_headings.docx: 제목 1~3이 여러 개 있고 본문이 있는 고정 샘플 워드.
가정(구현 방법이 정해지면 맞춰 고친다):
- `docmill.templates.register_template(name, source, home=...)`이 .docx도 받아 규칙 dict를 돌려준다.
  규칙 키는 FR-18과 같다 (`title_size`·`title_color`·`title_align`·`font`).
- apply_template 도구는 인자 `input`·`template`을 받고 JSON 결과에 `path`·`rules`를 가진다.
- 제목은 스타일 `Heading1`~`Heading3`(또는 `제목1`~`제목3`)를 쓰는 문단이다.
  크기·색·정렬·글꼴은 문단/런 속성(`w:sz`·`w:color`·`w:jc`·`w:rFonts`)
  또는 styles.xml의 스타일에서 읽는다.
- 모든 제목의 서식은 양식 워드의 제목 서식(같은 수준끼리)과 같아진다.
"""

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
NAME = "회사워드양식"
HEADING = re.compile(r"Heading([123])|제목\s?([123])")


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


def _read(docx: Path, part: str) -> str:
    with zipfile.ZipFile(docx) as z:
        return z.read(part).decode("utf-8")


def _paragraphs(docx: Path) -> list[str]:
    return re.findall(r"<w:p[ >].*?</w:p>", _read(docx, "word/document.xml"), re.S)


def _text(paragraph: str) -> str:
    return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", paragraph))


def _heading_level(paragraph: str) -> int | None:
    m = re.search(r'<w:pStyle w:val="([^"]+)"', paragraph)
    h = HEADING.fullmatch(m.group(1)) if m else None
    return int(h.group(1) or h.group(2)) if h else None


def _headings(docx: Path) -> list[tuple[int, dict[str, str | None]]]:
    """제목 문단마다 (수준, 서식). 문단/런 속성을 먼저 보고, 없으면 styles.xml 스타일에서 찾는다."""
    styles = _read(docx, "word/styles.xml")
    found: list[tuple[int, dict[str, str | None]]] = []
    for p in _paragraphs(docx):
        level = _heading_level(p)
        if level is None:
            continue
        style_id = re.search(r'<w:pStyle w:val="([^"]+)"', p).group(1)  # type: ignore[union-attr]
        sm = re.search(
            rf'<w:style [^>]*w:styleId="{re.escape(style_id)}".*?</w:style>', styles, re.S
        )
        style_xml = sm.group(0) if sm else ""

        def pick(pattern: str, p: str = p, style_xml: str = style_xml) -> str | None:
            m = re.search(pattern, p) or re.search(pattern, style_xml)
            return m.group(1) if m else None

        found.append(
            (
                level,
                {
                    "size": pick(r'<w:sz w:val="(\d+)"'),
                    "color": (pick(r'<w:color w:val="([0-9A-Fa-f]{6})"') or "").upper() or None,
                    "align": pick(r'<w:jc w:val="(\w+)"'),
                    "font": pick(r'<w:rFonts [^>]*w:ascii="([^"]+)"'),
                },
            )
        )
    return found


def _apply(home: Path) -> dict[str, Any]:
    from docmill import templates

    templates.register_template(NAME, FIXTURES / "template_word.docx", home=home)
    args = {"input": str(FIXTURES / "report_headings.docx"), "template": NAME}
    return asyncio.run(asyncio.wait_for(_call(home, "apply_template", args), 90))


@pytest.mark.fr("FR-25")
def test_word_template_registered_and_usable_by_name(tmp_path: Path) -> None:
    from docmill import templates

    rules = templates.register_template(NAME, FIXTURES / "template_word.docx", home=tmp_path)
    for key in ("title_size", "title_color", "title_align", "font"):
        assert rules.get(key), f"추출한 규칙에 {key}가 있어야 한다"
    assert NAME in templates.list_templates(home=tmp_path)
    out = _apply(tmp_path)
    assert "error" not in out, out
    assert Path(out["path"]).suffix == ".docx" and Path(out["path"]).exists()
    assert out["rules"]


@pytest.mark.fr("FR-25")
def test_all_headings_match_template_values(tmp_path: Path) -> None:
    out = _apply(tmp_path)
    assert "error" not in out, out
    expected = dict(_headings(FIXTURES / "template_word.docx"))
    assert expected, "양식 워드에 제목 스타일이 있어야 한다"
    actual = _headings(Path(out["path"]))
    assert len(actual) >= 3
    for level, fmt in actual:
        assert all(fmt.values()), f"제목 {level}의 서식이 비어 있다: {fmt}"
        assert fmt == expected[level], f"제목 {level}이 양식과 다르다"


@pytest.mark.fr("FR-25")
def test_body_text_is_unchanged(tmp_path: Path) -> None:
    out = _apply(tmp_path)
    assert "error" not in out, out
    src = FIXTURES / "report_headings.docx"
    before = [_text(p) for p in _paragraphs(src) if _heading_level(p) is None]
    after = [_text(p) for p in _paragraphs(Path(out["path"])) if _heading_level(p) is None]
    assert before and after == before
