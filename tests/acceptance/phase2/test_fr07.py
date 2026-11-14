"""FR-07 apply_template 업무 도구 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/company_intro.pptx (있음): 양식을 적용할 PPT
- tests/fixtures/template_default.pptx (있음): 제목 40pt·`1F4E79`·가운데·Pretendard.
  company_intro.pptx(3장, 제목 32pt·`333333`·왼쪽·Arial)와 다르다. 제목 서식은 슬라이드 제목 도형의
  런/문단 속성(`sz`·`srgbClr`·`algn`)에 직접 지정돼 있다.
가정(구현 방법이 정해지면 맞춰 고친다):
- 양식은 FR-18의 `docmill.templates.register_template(name, source, home=...)`로 등록한다.
- 도구 인자: `input`, `template`(양식 이름), 선택 `out_dir`.
- 도구 결과는 JSON 텍스트이며 `path`, `pages_modified`(수정한 페이지 수),
  `rules`(적용 규칙 목록)를 가진다.
- 슬라이드 제목은 `type="title"` 또는 `type="ctrTitle"` 플레이스홀더다.
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
TEMPLATE_NAME = "default"


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


def _apply(home: Path) -> dict[str, Any]:
    from docmill import templates

    templates.register_template(TEMPLATE_NAME, FIXTURES / "template_default.pptx", home=home)
    args = {"input": str(FIXTURES / "company_intro.pptx"), "template": TEMPLATE_NAME}
    return asyncio.run(asyncio.wait_for(_call(home, "apply_template", args), 90))


def _slides(pptx: Path) -> list[str]:
    with zipfile.ZipFile(pptx) as z:
        names = sorted(
            (n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.search(r"\d+", n.split("/")[-1]).group()),  # type: ignore[union-attr]
        )
        return [z.read(n).decode("utf-8") for n in names]


def _title_shape(xml: str) -> str | None:
    for sp in re.findall(r"<p:sp>.*?</p:sp>", xml, re.S):
        if re.search(r'<p:ph[^>]*type="(title|ctrTitle)"', sp):
            return sp
    return None


def _title_style(xml: str) -> tuple[str | None, str | None, str | None]:
    sp = _title_shape(xml)
    if sp is None:
        return (None, None, None)
    size = re.search(r'<a:rPr[^>]*\bsz="(\d+)"', sp)
    color = re.search(r'<a:rPr[^>]*>.*?<a:srgbClr val="([0-9A-Fa-f]{6})"', sp, re.S)
    align = re.search(r'<a:pPr[^>]*\balgn="(\w+)"', sp)
    return (
        size.group(1) if size else None,
        color.group(1).upper() if color else None,
        align.group(1) if align else None,
    )


def _body_texts(xml: str) -> list[str]:
    title = _title_shape(xml)
    rest = xml.replace(title, "") if title else xml
    return re.findall(r"<a:t>([^<]*)</a:t>", rest)


@pytest.mark.fr("FR-07")
def test_all_slide_titles_match_template_style(tmp_path: Path) -> None:
    out = _apply(tmp_path)
    expected = _title_style(_slides(FIXTURES / "template_default.pptx")[0])
    assert all(expected), "양식 샘플의 제목 서식(크기·색·정렬)을 읽지 못했다"
    result_slides = _slides(Path(out["path"]))
    assert result_slides
    for i, xml in enumerate(result_slides, 1):
        assert _title_style(xml) == expected, f"{i}번 슬라이드 제목 서식이 양식과 다르다"


@pytest.mark.fr("FR-07")
def test_body_text_unchanged(tmp_path: Path) -> None:
    out = _apply(tmp_path)
    before = [_body_texts(x) for x in _slides(FIXTURES / "company_intro.pptx")]
    after = [_body_texts(x) for x in _slides(Path(out["path"]))]
    assert after == before


@pytest.mark.fr("FR-07")
def test_result_reports_modified_pages_and_rules(tmp_path: Path) -> None:
    out = _apply(tmp_path)
    assert out["pages_modified"] >= 1
    assert isinstance(out["rules"], list) and len(out["rules"]) >= 1
