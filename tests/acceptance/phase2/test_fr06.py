"""FR-06 doc_to_task_table 업무 도구 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/meeting_1111.docx (있음): "할 일" 제목 아래 글머리표 6개.
  형식 `할 일: … / 담당: … / 마감: YYYY-MM-DD`. 4개는 담당·마감이 모두 있고(마감일 순서 섞임),
  1개는 마감, 1개는 담당이 빠져 있다.
가정(구현 방법이 정해지면 맞춰 고친다):
- 도구 인자: `input`. 선택 인자: `out_dir`.
- 도구 결과는 JSON 텍스트이며 `path`(xlsx), `excluded`(목록; 각 항목에 `item`·`reason`)를 가진다.
- xlsx 첫 시트 첫 행은 머리글 "할 일·담당자·마감일·상태"이고 마감일은 YYYY-MM-DD 문자열이다.
- 엑셀 값은 표준 라이브러리(zipfile)로 직접 읽는다 (의존성 추가 없음).
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
from xml.etree import ElementTree as ET

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
HEADERS = ["할 일", "담당자", "마감일", "상태"]


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


def _run(home: Path) -> dict[str, Any]:
    args = {"input": str(FIXTURES / "meeting_1111.docx")}
    return asyncio.run(asyncio.wait_for(_call(home, "doc_to_task_table", args), 90))


def _read_rows(xlsx: Path) -> list[list[str]]:
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


@pytest.mark.fr("FR-06")
def test_four_tasks_saved_with_expected_columns(tmp_path: Path) -> None:
    out = _run(tmp_path)
    path = Path(out["path"])
    assert path.suffix == ".xlsx" and path.exists()
    rows = _read_rows(path)
    assert rows[0][:4] == HEADERS
    body = [r for r in rows[1:] if any(r)]
    assert len(body) == 4
    assert all(r[0] and r[1] and r[2] for r in body), "할 일·담당자·마감일이 모두 채워져야 한다"


@pytest.mark.fr("FR-06")
def test_rows_sorted_by_due_date(tmp_path: Path) -> None:
    out = _run(tmp_path)
    body = [r for r in _read_rows(Path(out["path"]))[1:] if any(r)]
    dates = [r[2] for r in body]
    assert dates == sorted(dates)


@pytest.mark.fr("FR-06")
def test_items_without_owner_or_due_date_are_excluded_with_reason(tmp_path: Path) -> None:
    out = _run(tmp_path)
    excluded = out["excluded"]
    assert len(excluded) >= 1
    for entry in excluded:
        assert entry["item"]
        assert entry["reason"]
    body = [r for r in _read_rows(Path(out["path"]))[1:] if any(r)]
    saved = {r[0] for r in body}
    assert not saved & {e["item"] for e in excluded}, "제외된 항목이 표에 들어가면 안 된다"
