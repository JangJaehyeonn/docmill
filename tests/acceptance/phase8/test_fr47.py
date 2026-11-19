"""FR-47 근거 있는 문서 (문장 출처) 인수 테스트 (초안).

필요한 샘플: tests/fixtures/ 의 meeting_1111.docx, sales_q3.xlsx (있음).
가정(구현 방법이 정해지면 맞춰 고친다):
- provenance.json(`<결과 파일 이름>.provenance.json`)에 `sentences` 목록이 새로 생긴다(기존 `numbers`는 그대로).
  각 항목: `text`(문장), `location`(결과 문서 위치), `source`(근거).
  - doc_to_task_table: 결과 xlsx의 행마다 한 항목. `location.row`(시트 행 번호), `source.file`(회의록 경로),
    `source.paragraph`(회의록 docx `word/document.xml`의 `w:p` 순번, 0부터).
  - excel_to_report_ppt: 요약 문장마다 한 항목. `location.slide`·`location.shape`,
    `source.file`(원본 xlsx), `source.range`(예: `C2:C4`).
- review_map은 출처 없는 문장(provenance `sentences`에 없는 문장)을 `reason: "출처 없음"`으로 돌려준다.
- studio: 작업 폴더에 sales_q3.xlsx와 보고 pptx(+provenance)가 있고 pptx 탭이 열려 있을 때,
  `region` "문장 출처" 안에 문장마다 `button`(이름이 문장)이 있다. 누르면 원본 xlsx 탭이 활성 탭
  (`aria-selected="true"`)이 되고 `[data-role="source-highlight"]`에 근거 범위 문구가 보인다.
  ONLYOFFICE가 필요하므로 DOCMILL_ONLYOFFICE_URL이 없으면 실패한다.
- 이 파일은 tests/acceptance/ 로 옮겨졌을 때 conftest의 start_studio·page 픽스처를 쓴다.
"""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE = FIXTURES / "sales_q3.xlsx"
MEETING = FIXTURES / "meeting_1111.docx"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_RANGE = re.compile(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?")
UNSOURCED = "이번 분기 매출은 크게 개선되었다."

_FR = "FR-47"
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


def _sync(home: Path, name: str, args: dict[str, Any]) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, asyncio.wait_for(_call(home, name, args), 90)).result()


def _report(home: Path) -> Path:
    out = _sync(
        home,
        "excel_to_report_ppt",
        {
            "input": str(SAMPLE),
            "group_by": "부서",
            "value": "매출",
            "slides": 5,
            "chart": "bar",
            "verify": False,
        },
    )
    return Path(out["path"])


def _prov_path(doc: Path) -> Path:
    return doc.with_name(doc.name + ".provenance.json")


def _sentences(doc: Path) -> list[dict[str, Any]]:
    return list(json.loads(_prov_path(doc).read_text(encoding="utf-8"))["sentences"])


def _xlsx_rows(xlsx: Path) -> list[list[str]]:
    with zipfile.ZipFile(xlsx) as z:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            strings = ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")) for si in root]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows: list[list[str]] = []
    for row in sheet.iter(f"{{{NS['m']}}}row"):
        cells: dict[int, str] = {}
        for c in row.findall("m:c", NS):
            col = re.match(r"[A-Z]+", c.get("r", ""))
            if col is None:
                continue
            idx = ord(col.group()[0]) - ord("A")
            v = c.find("m:v", NS)
            inline = c.find("m:is", NS)
            if inline is not None:
                cells[idx] = "".join(t.text or "" for t in inline.iter(f"{{{NS['m']}}}t"))
            elif v is not None and v.text is not None:
                cells[idx] = strings[int(v.text)] if c.get("t") == "s" else v.text
        rows.append([cells.get(i, "") for i in range(max(cells, default=-1) + 1)])
    return rows


def _docx_paragraphs(docx: Path) -> list[str]:
    with zipfile.ZipFile(docx) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    return ["".join(t.text or "" for t in p.iter(f"{_W}t")) for p in root.iter(f"{_W}p")]


def _slide_shapes(pptx: Path) -> dict[tuple[int, str], str]:
    found: dict[tuple[int, str], str] = {}
    with zipfile.ZipFile(pptx) as z:
        for name in z.namelist():
            m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name)
            if not m:
                continue
            for sp in ET.fromstring(z.read(name)).iter(f"{_P}sp"):
                c_nv = next(sp.iter(f"{_P}cNvPr"), None)
                if c_nv is not None:
                    found[(int(m.group(1)), str(c_nv.get("id")))] = "\n".join(
                        t.text or "" for t in sp.iter(f"{_A}t")
                    )
    return found


def _inject_unsourced(src: Path, dst: Path) -> None:
    shape = (
        '<p:sp><p:nvSpPr><p:cNvPr id="900" name="Injected"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        "<p:spPr/><p:txBody><a:bodyPr/><a:p><a:r><a:t>"
        + UNSOURCED
        + "</a:t></a:r></a:p></p:txBody></p:sp>"
    )
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "ppt/slides/slide1.xml":
                data = data.replace(b"</p:spTree>", shape.encode("utf-8") + b"</p:spTree>", 1)
            zout.writestr(item, data)
    shutil.copy(_prov_path(src), _prov_path(dst))


@pytest.mark.fr("FR-47")
def test_task_table_rows_record_meeting_paragraph(tmp_path: Path) -> None:
    out = _sync(tmp_path, "doc_to_task_table", {"input": str(MEETING)})
    xlsx = Path(out["path"])
    rows = [r for r in _xlsx_rows(xlsx)[1:] if any(r)]
    assert rows
    entries = _sentences(xlsx)
    assert len(entries) == len(rows), "행마다 근거가 하나씩 있어야 한다"
    paragraphs = _docx_paragraphs(MEETING)
    by_row = {int(e["location"]["row"]): e for e in entries}
    for offset, row in enumerate(rows):
        entry = by_row[offset + 2]  # 1행은 머리글
        assert str(entry["source"]["file"]).endswith("meeting_1111.docx"), entry
        assert row[0] in paragraphs[int(entry["source"]["paragraph"])], (row, entry)


@pytest.mark.fr("FR-47")
def test_report_summary_sentences_record_source_range(tmp_path: Path) -> None:
    report = _report(tmp_path)
    entries = _sentences(report)
    assert entries, "요약 문장마다 근거가 기록돼야 한다"
    shapes = _slide_shapes(report)
    for e in entries:
        loc = e["location"]
        assert e["text"] in shapes[(int(loc["slide"]), str(loc["shape"]))], e
        assert str(e["source"]["file"]).endswith("sales_q3.xlsx"), e
        m = _RANGE.fullmatch(str(e["source"]["range"]))
        assert m, e
        assert 1 <= int(m.group(2)) <= int(m.group(4) or m.group(2)) <= 16, e


@pytest.mark.fr("FR-47")
def test_review_map_flags_sentence_without_source(tmp_path: Path) -> None:
    report = _report(tmp_path)
    injected = tmp_path / "injected.pptx"
    _inject_unsourced(report, injected)
    out = _sync(tmp_path, "review_map", {"document": str(injected)})
    flagged = [i for i in out["items"] if UNSOURCED in str(i.get("text", ""))]
    assert flagged and all(i["reason"] == "출처 없음" for i in flagged), out["items"]
    sourced = {e["text"] for e in _sentences(report)}
    assert not [i for i in out["items"] if str(i.get("text", "")) in sourced], (
        "근거 있는 문장은 걸리면 안 된다"
    )


@pytest.mark.fr("FR-47")
def test_studio_click_sentence_activates_source_tab_and_highlights(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    assert os.environ.get("DOCMILL_ONLYOFFICE_URL"), "DOCMILL_ONLYOFFICE_URL이 필요하다"
    ws = tmp_path / "ws"
    ws.mkdir()
    src = ws / "sales_q3.xlsx"
    shutil.copy(SAMPLE, src)
    out = _sync(
        tmp_path,
        "excel_to_report_ppt",
        {
            "input": str(src),
            "group_by": "부서",
            "value": "매출",
            "slides": 5,
            "chart": "bar",
            "verify": False,
        },
    )
    report = Path(out["path"])
    shutil.copy(report, ws / "report.pptx")
    shutil.copy(_prov_path(report), ws / "report.pptx.provenance.json")
    entry = _sentences(ws / "report.pptx")[0]
    page.goto(start_studio(workspace=ws))
    for name in ("sales_q3.xlsx", "report.pptx"):
        page.get_by_role("listitem").filter(has_text=name).first.click()
        page.get_by_role("tab", name=name).wait_for()
    page.get_by_role("region", name="문장 출처").get_by_role("button", name=entry["text"]).click()
    page.get_by_role("tab", name="sales_q3.xlsx", selected=True).wait_for(timeout=30_000)
    highlight = page.locator('[data-role="source-highlight"]')
    highlight.first.wait_for(timeout=30_000)
    assert str(entry["source"]["range"]) in highlight.first.inner_text()
