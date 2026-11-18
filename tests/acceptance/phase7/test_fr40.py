"""FR-40 문서 안 숫자 모순 찾기 (find_contradictions) 인수 테스트 (초안).

필요한 샘플: 없음. docx·pptx는 테스트가 표준 라이브러리 zipfile로 직접 만든다.
가정(구현 방법이 정해지면 맞춰 고친다):
- MCP 도구 `find_contradictions` 인자: `document`(docx·pptx).
- 결과 JSON: `contradictions` 목록. 각 항목은 `kind`(`합계 불일치`·`비율 불일치`·`항목 불일치`),
  `locations`(목록, 항목마다 위치 문자열 또는 {location, text}), `expected`(계산한 값), `actual`(문서에 적힌 값).
- 위치 문자열에는 표 셀이면 `표 1`·행·열, 슬라이드면 `슬라이드 N`이 들어간다.
"""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_FR = "FR-40"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)

_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def _make_docx(path: Path, blocks: list[str | list[list[str]]]) -> Path:
    """문자열은 문단, 2차원 목록은 표로 만든다."""
    parts = []
    for b in blocks:
        if isinstance(b, str):
            parts.append(f"<w:p><w:r><w:t>{b}</w:t></w:r></w:p>")
        else:
            rows = "".join(
                "<w:tr>"
                + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row)
                + "</w:tr>"
                for row in b
            )
            parts.append(f"<w:tbl>{rows}</w:tbl>")
    doc = f'{_HEAD}<w:document xmlns:w="{_W}"><w:body>{"".join(parts)}</w:body></w:document>'
    ct = (
        f'{_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        f'{_HEAD}<Relationships xmlns="{_PKG}"><Relationship Id="rId1" Type="{_REL}/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return path


def _make_pptx(path: Path, slides: list[list[str]]) -> Path:
    """슬라이드마다 문단 목록을 가진 최소 pptx."""
    n = len(slides)
    ct = (
        f'{_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        + "".join(
            f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            for i in range(1, n + 1)
        )
        + "</Types>"
    )
    root_rels = f'{_HEAD}<Relationships xmlns="{_PKG}"><Relationship Id="rId1" Type="{_REL}/officeDocument" Target="ppt/presentation.xml"/></Relationships>'
    ids = "".join(f'<p:sldId id="{255 + i}" r:id="rId{i}"/>' for i in range(1, n + 1))
    pres = (
        f'{_HEAD}<p:presentation xmlns:p="{_P}" xmlns:r="{_REL}" xmlns:a="{_A}">'
        f'<p:sldIdLst>{ids}</p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/></p:presentation>'
    )
    pres_rels = (
        f'{_HEAD}<Relationships xmlns="{_PKG}">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="{_REL}/slide" Target="slides/slide{i}.xml"/>'
            for i in range(1, n + 1)
        )
        + "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("ppt/presentation.xml", pres)
        z.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
        for i, paras in enumerate(slides, start=1):
            body = "".join(f"<a:p><a:r><a:t>{p}</a:t></a:r></a:p>" for p in paras)
            z.writestr(
                f"ppt/slides/slide{i}.xml",
                f'{_HEAD}<p:sld xmlns:p="{_P}" xmlns:a="{_A}"><p:cSld><p:spTree>'
                '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
                f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr/>'
                f"<p:txBody><a:bodyPr/>{body}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>",
            )
    return path


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


def _find(home: Path, doc: Path) -> list[dict[str, Any]]:
    out = asyncio.run(asyncio.wait_for(_call(home, "find_contradictions", {"document": str(doc)}), 90))
    return list(out["contradictions"])


# 항목 합 = 100 + 200 + 300 = 600
GOOD_TABLE = [["부서", "매출"], ["영업1팀", "100"], ["영업2팀", "200"], ["마케팅팀", "300"], ["합계", "600"]]
BAD_TABLE = [["부서", "매출"], ["영업1팀", "100"], ["영업2팀", "200"], ["마케팅팀", "300"], ["합계", "650"]]
# 전분기 → 이번 분기: 200 → 250 이면 25% 증가
GROWTH_TABLE = [["구분", "전분기", "이번 분기"], ["매출", "200", "250"]]


@pytest.mark.fr("FR-40")
def test_wrong_total_cell_is_reported_with_calculated_value(tmp_path: Path) -> None:
    doc = _make_docx(tmp_path / "a.docx", ["매출 현황", BAD_TABLE])
    found = [c for c in _find(tmp_path, doc) if c["kind"] == "합계 불일치"]
    assert len(found) == 1, found
    assert found[0]["locations"], found
    assert float(str(found[0]["expected"]).replace(",", "")) == 600
    assert float(str(found[0]["actual"]).replace(",", "")) == 650


@pytest.mark.fr("FR-40")
def test_prose_percent_that_disagrees_with_table_is_found(tmp_path: Path) -> None:
    doc = _make_docx(tmp_path / "b.docx", ["이번 분기 매출은 전분기보다 30% 증가했다.", GROWTH_TABLE])
    found = [c for c in _find(tmp_path, doc) if c["kind"] == "비율 불일치"]
    assert len(found) == 1, found
    assert float(str(found[0]["expected"]).replace("%", "")) == pytest.approx(25)
    assert float(str(found[0]["actual"]).replace("%", "")) == pytest.approx(30)


@pytest.mark.fr("FR-40")
def test_same_item_with_different_numbers_on_two_slides_returns_both_locations(tmp_path: Path) -> None:
    doc = _make_pptx(
        tmp_path / "c.pptx",
        [["영업1팀 매출은 1,200만원이다."], ["다른 이야기"], ["영업1팀 매출은 1,350만원이다."]],
    )
    found = [c for c in _find(tmp_path, doc) if c["kind"] == "항목 불일치"]
    assert len(found) == 1, found
    text = json.dumps(found[0]["locations"], ensure_ascii=False)
    assert "슬라이드 1" in text and "슬라이드 3" in text


@pytest.mark.fr("FR-40")
def test_consistent_samples_give_empty_list(tmp_path: Path) -> None:
    docx = _make_docx(
        tmp_path / "ok.docx", ["이번 분기 매출은 전분기보다 25% 증가했다.", GROWTH_TABLE, GOOD_TABLE]
    )
    pptx = _make_pptx(
        tmp_path / "ok.pptx", [["영업1팀 매출은 1,200만원이다."], ["영업1팀 매출은 1,200만원이다."]]
    )
    assert _find(tmp_path, docx) == []
    assert _find(tmp_path, pptx) == []
