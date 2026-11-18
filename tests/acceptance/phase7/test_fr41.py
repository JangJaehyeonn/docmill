"""FR-41 버전 비교 요약 (compare_versions) 인수 테스트 (초안).

필요한 샘플: FIXTURES.md 의 `meeting_1111_v2.hwp` (tests/fixtures/meeting_1111.hwp에서
숫자 2개와 문장 1개를 바꾼 버전). pptx·xlsx·docx는 테스트가 zipfile로 직접 만든다.
가정(구현 방법이 정해지면 맞춰 고친다):
- MCP 도구 `compare_versions` 인자: `before`, `after`(같은 형식의 두 파일 경로).
- 결과 JSON: `changes` 목록. 각 항목은 `kind`(`숫자`·`문장`·`표 셀`), `location`, `old`, `new`.
  표 셀의 값이 숫자여도 숫자 변경으로 센다 (xlsx 셀 포함).
"""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

_FR = "FR-41"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)

_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELS_CT = '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'

# (문단들, 숫자 2개와 문장 1개를 바꾼 문단들)
V1 = ["이번 달 매출은 1200만원이다.", "참석자는 8명이다.", "다음 회의에서 예산을 논의한다."]
V2 = ["이번 달 매출은 1500만원이다.", "참석자는 10명이다.", "다음 회의에서 인력을 논의한다."]


def _write_docx(path: Path, paras: list[str]) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paras)
    ct = (
        f'{_HEAD}<Types xmlns="{_CT}">{_RELS_CT}'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", f'{_HEAD}<Relationships xmlns="{_PKG}"><Relationship Id="rId1" Type="{_REL}/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", f'{_HEAD}<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>')
    return path


def _write_pptx(path: Path, paras: list[str]) -> Path:
    """문단마다 슬라이드 하나."""
    n = len(paras)
    ct = (
        f'{_HEAD}<Types xmlns="{_CT}">{_RELS_CT}'
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        + "".join(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' for i in range(1, n + 1))
        + "</Types>"
    )
    ids = "".join(f'<p:sldId id="{255 + i}" r:id="rId{i}"/>' for i in range(1, n + 1))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", f'{_HEAD}<Relationships xmlns="{_PKG}"><Relationship Id="rId1" Type="{_REL}/officeDocument" Target="ppt/presentation.xml"/></Relationships>')
        z.writestr("ppt/presentation.xml", f'{_HEAD}<p:presentation xmlns:p="{_P}" xmlns:r="{_REL}" xmlns:a="{_A}"><p:sldIdLst>{ids}</p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/></p:presentation>')
        z.writestr("ppt/_rels/presentation.xml.rels", f'{_HEAD}<Relationships xmlns="{_PKG}">' + "".join(f'<Relationship Id="rId{i}" Type="{_REL}/slide" Target="slides/slide{i}.xml"/>' for i in range(1, n + 1)) + "</Relationships>")
        for i, p in enumerate(paras, start=1):
            z.writestr(
                f"ppt/slides/slide{i}.xml",
                f'{_HEAD}<p:sld xmlns:p="{_P}" xmlns:a="{_A}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
                f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:p><a:r><a:t>{p}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
            )
    return path


def _write_xlsx(path: Path, paras: list[str]) -> Path:
    """문단마다 A열 한 행(문자열 셀)."""
    rows = "".join(f'<row r="{i}"><c r="A{i}" t="inlineStr"><is><t>{p}</t></is></c></row>' for i, p in enumerate(paras, start=1))
    ct = (
        f'{_HEAD}<Types xmlns="{_CT}">{_RELS_CT}'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", f'{_HEAD}<Relationships xmlns="{_PKG}"><Relationship Id="rId1" Type="{_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", f'{_HEAD}<workbook xmlns="{_S}" xmlns:r="{_REL}"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", f'{_HEAD}<Relationships xmlns="{_PKG}"><Relationship Id="rId1" Type="{_REL}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'{_HEAD}<worksheet xmlns="{_S}"><sheetData>{rows}</sheetData></worksheet>')
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


def _compare(home: Path, before: Path, after: Path) -> list[dict[str, Any]]:
    args = {"before": str(before), "after": str(after)}
    return list(asyncio.run(asyncio.wait_for(_call(home, "compare_versions", args), 120))["changes"])


def _check_three(changes: list[dict[str, Any]]) -> None:
    assert len(changes) == 3, changes
    assert all(c["location"] for c in changes)
    pairs = {(str(c["old"]), str(c["new"])) for c in changes}
    joined = json.dumps(changes, ensure_ascii=False)
    for old, new in (("1200", "1500"), ("8", "10")):
        assert any(old in o and new in n for o, n in pairs), (old, new, pairs)
    assert "예산" in joined and "인력" in joined
    assert sum(1 for c in changes if c["kind"] == "문장") >= 1 or any("논의" in str(c["old"]) for c in changes)


_WRITERS = {"docx": _write_docx, "pptx": _write_pptx, "xlsx": _write_xlsx}


@pytest.mark.fr("FR-41")
@pytest.mark.parametrize("ext", ["docx", "pptx", "xlsx"])
def test_two_numbers_and_one_sentence_are_listed_exactly(tmp_path: Path, ext: str) -> None:
    a = _WRITERS[ext](tmp_path / f"a.{ext}", V1)
    b = _WRITERS[ext](tmp_path / f"b.{ext}", V2)
    _check_three(_compare(tmp_path, a, b))


@pytest.mark.fr("FR-41")
def test_hwp_versions_are_compared(tmp_path: Path) -> None:
    changes = _compare(tmp_path, FIXTURES / "meeting_1111.hwp", FIXTURES / "meeting_1111_v2.hwp")
    assert len(changes) == 3, changes
    assert all(c["location"] and "old" in c and "new" in c for c in changes)


@pytest.mark.fr("FR-41")
@pytest.mark.parametrize("ext", ["docx", "pptx", "xlsx"])
def test_same_file_twice_gives_empty_list(tmp_path: Path, ext: str) -> None:
    a = _WRITERS[ext](tmp_path / f"a.{ext}", V1)
    b = tmp_path / f"copy.{ext}"
    shutil.copy(a, b)
    assert _compare(tmp_path, a, b) == []


@pytest.mark.fr("FR-41")
def test_same_hwp_twice_gives_empty_list(tmp_path: Path) -> None:
    hwp = FIXTURES / "meeting_1111.hwp"
    copy = tmp_path / "copy.hwp"
    shutil.copy(hwp, copy)
    assert _compare(tmp_path, hwp, copy) == []
