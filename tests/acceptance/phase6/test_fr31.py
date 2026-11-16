"""FR-31 AI 편집 범위 지키기 (guarded_edit) 인수 테스트 (초안).

필요한 샘플: tests/fixtures/ 의 company_intro.pptx(3장, 2장에 제목 id=2·본문 id=3 도형), sales_q3.xlsx,
meeting_1111.docx (모두 있음).
가정(구현 방법이 정해지면 맞춰 고친다):
- MCP 도구 `guarded_edit` 인자: `input`, `scope`, `edit`, 선택 `out_dir`.
  - pptx: scope `{"slides": [2], "shapes": [2]}` (슬라이드 번호, 도형 cNvPr id)
    edit `{"set_text": [{"slide": 2, "shape": 2, "text": "새 제목"}]}`
  - xlsx: scope `{"sheet": 1, "range": "C2:C4"}`, edit `{"set_cells": {"C2": 999}}` (시트는 1부터 번호)
  - docx: scope `{"paragraphs": [3]}` (본문 문단 번호, 0부터), edit `{"set_paragraphs": {"3": "새 문장"}}`
  - 범위 밖을 건드리는 편집은 같은 형식에서 scope 밖 대상(슬라이드 3 도형 2, 셀 C10, 문단 5)을 함께 적어 만든다.
    도구는 편집을 적용한 뒤 결과를 원본과 비교해 범위 밖에서 바뀐 곳을 되돌리고 목록으로 알려야 한다.
    (AI에 노출되는 도구이므로 범위 검사를 끄는 인자는 두지 않는다.)
- 결과 JSON: `path`, `out_of_scope_changes`(정수), `reverted`(목록, 각 항목에 `location`), `summary`(문구).
  변경이 없으면 summary에 '범위 밖 변경 0곳'이 들어 있다.
- 동작 확인은 결과 파일 zip의 XML을 표준 라이브러리로 직접 비교한다.
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
PPTX = FIXTURES / "company_intro.pptx"
XLSX = FIXTURES / "sales_q3.xlsx"
DOCX = FIXTURES / "meeting_1111.docx"

_FR = "FR-31"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


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


def _edit(home: Path, src: Path, scope: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any]:
    args = {"input": str(src), "scope": scope, "edit": edit}
    return asyncio.run(asyncio.wait_for(_call(home, "guarded_edit", args), 120))


def _parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist() if n.endswith(".xml")}


def _sp(xml: bytes, shape_id: str) -> str:
    text = xml.decode("utf-8")
    for m in re.finditer(r"<p:sp>.*?</p:sp>", text, re.S):
        if f'<p:cNvPr id="{shape_id}"' in m.group(0):
            return m.group(0)
    raise AssertionError(f"도형 {shape_id}가 없다")


def _paragraphs(doc_xml: bytes) -> list[str]:
    # 본문 문단(w:body 바로 아래 w:p)의 글자만 순서대로 모은다.
    import xml.etree.ElementTree as ET

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = ET.fromstring(doc_xml).find(f"{w}body")
    assert body is not None
    return ["".join(t.text or "" for t in p.iter(f"{w}t")) for p in body.findall(f"{w}p")]


PPT_SCOPE = {"slides": [2], "shapes": [2]}
PPT_EDIT = {"set_text": [{"slide": 2, "shape": 2, "text": "새 제목"}]}
PPT_LEAK = {
    "set_text": [
        {"slide": 2, "shape": 2, "text": "새 제목"},
        {"slide": 3, "shape": 2, "text": "몰래 바꾼 제목"},
        {"slide": 2, "shape": 3, "text": "몰래 바꾼 본문"},
    ],
}


@pytest.mark.fr("FR-31")
def test_pptx_title_edit_leaves_everything_else_identical(tmp_path: Path) -> None:
    out = _edit(tmp_path, PPTX, PPT_SCOPE, PPT_EDIT)
    assert out["out_of_scope_changes"] == 0
    assert "범위 밖 변경 0곳" in out["summary"]
    before, after = _parts(PPTX), _parts(Path(out["path"]))
    assert "새 제목" in after["ppt/slides/slide2.xml"].decode("utf-8")
    assert before["ppt/slides/slide1.xml"] == after["ppt/slides/slide1.xml"]
    assert before["ppt/slides/slide3.xml"] == after["ppt/slides/slide3.xml"]
    assert _sp(before["ppt/slides/slide2.xml"], "3") == _sp(after["ppt/slides/slide2.xml"], "3")


@pytest.mark.fr("FR-31")
def test_pptx_out_of_scope_changes_are_reverted_and_listed(tmp_path: Path) -> None:
    out = _edit(tmp_path, PPTX, PPT_SCOPE, PPT_LEAK)
    assert out["out_of_scope_changes"] == 2
    assert len(out["reverted"]) == 2
    assert all(r["location"] for r in out["reverted"])
    before, after = _parts(PPTX), _parts(Path(out["path"]))
    assert before["ppt/slides/slide3.xml"] == after["ppt/slides/slide3.xml"]
    assert _sp(before["ppt/slides/slide2.xml"], "3") == _sp(after["ppt/slides/slide2.xml"], "3")
    assert "새 제목" in after["ppt/slides/slide2.xml"].decode("utf-8"), "범위 안 편집은 남는다"
    assert "몰래" not in "".join(v.decode("utf-8", "ignore") for v in after.values())


@pytest.mark.fr("FR-31")
def test_xlsx_cell_range_is_guarded(tmp_path: Path) -> None:
    ok = _edit(tmp_path, XLSX, {"sheet": 1, "range": "C2:C4"}, {"set_cells": {"C2": 999}})
    assert ok["out_of_scope_changes"] == 0
    leak = _edit(
        tmp_path,
        XLSX,
        {"sheet": 1, "range": "C2:C4"},
        {"set_cells": {"C2": 999, "C10": 111}},
    )
    assert leak["out_of_scope_changes"] >= 1
    assert any("C10" in str(r["location"]) for r in leak["reverted"])
    sheet = _parts(Path(leak["path"]))["xl/worksheets/sheet1.xml"].decode("utf-8")
    original = _parts(XLSX)["xl/worksheets/sheet1.xml"].decode("utf-8")
    c10 = re.search(r'<c r="C10".*?</c>', sheet, re.S)
    c10_before = re.search(r'<c r="C10".*?</c>', original, re.S)
    assert c10 and c10_before and c10.group(0) == c10_before.group(0)
    c2 = re.search(r'<c r="C2"[^>]*>.*?<v>([^<]+)</v>', sheet, re.S)
    assert c2 and float(c2.group(1)) == 999, "범위 안 셀 C2는 요청한 값이어야 한다"


@pytest.mark.fr("FR-31")
def test_docx_paragraph_range_is_guarded(tmp_path: Path) -> None:
    ok = _edit(tmp_path, DOCX, {"paragraphs": [3]}, {"set_paragraphs": {"3": "새 문장입니다"}})
    assert ok["out_of_scope_changes"] == 0
    leak = _edit(
        tmp_path,
        DOCX,
        {"paragraphs": [3]},
        {"set_paragraphs": {"3": "새 문장입니다", "5": "몰래 바꾼 문단"}},
    )
    assert leak["out_of_scope_changes"] >= 1 and leak["reverted"]
    after = _paragraphs(_parts(Path(leak["path"]))["word/document.xml"])
    before = _paragraphs(_parts(DOCX)["word/document.xml"])
    assert after[3] == "새 문장입니다", "범위 안 문단 3은 요청한 문장이어야 한다"
    assert after[5] == before[5], "범위 밖 문단 5는 원문으로 돌아와야 한다"
    assert len(after) == len(before)


@pytest.mark.fr("FR-31")
def test_same_input_gives_byte_identical_output(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    pa = Path(_edit(a, PPTX, PPT_SCOPE, PPT_LEAK)["path"])
    pb = Path(_edit(b, PPTX, PPT_SCOPE, PPT_LEAK)["path"])
    assert pa.read_bytes() == pb.read_bytes()
