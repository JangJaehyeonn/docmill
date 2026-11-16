"""FR-35 한글 파일도 업무 도구에서 쓰기 인수 테스트 (초안).

필요한 샘플: tests/fixtures/meeting_1111.hwp (있음, meeting_1111.docx와 같은 문단 11개), meeting_1111.docx (있음).
읽을 수 없는 hwp는 테스트가 깨진 바이트로 만든다.
가정(구현 방법이 정해지면 맞춰 고친다):
- doc_to_task_table는 `input`에 hwp/hwpx를 받는다. 결과는 docx 입력과 같은 형식이다 (FR-06: `path`, `excluded`).
- preflight는 FR-34 형식(`checks`: name·status), review_map은 FR-33 형식(`items`: reason·text)이고
  인자는 `document`다. 같은 내용이면 hwp와 docx의 결과가 같은 규칙으로 나와야 하므로 항목(이유·상태)이 같다.
- 실패는 다른 업무 도구처럼 `{"error": 이유, "step": 단계}` JSON으로 돌려준다.
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
from xml.etree import ElementTree as ET

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
HWP = FIXTURES / "meeting_1111.hwp"
DOCX = FIXTURES / "meeting_1111.docx"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

_FR = "FR-35"
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
    return asyncio.run(asyncio.wait_for(_call(home, name, args), 120))


def _rows(xlsx: Path) -> list[list[str]]:
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


@pytest.mark.fr("FR-35")
def test_hwp_gives_same_task_table_and_exclusions_as_docx(tmp_path: Path) -> None:
    a = tmp_path / "docx"
    b = tmp_path / "hwp"
    a.mkdir()
    b.mkdir()
    from_docx = _sync(a, "doc_to_task_table", {"input": str(DOCX)})
    from_hwp = _sync(b, "doc_to_task_table", {"input": str(HWP)})
    assert "error" not in from_hwp, from_hwp
    assert _rows(Path(from_hwp["path"])) == _rows(Path(from_docx["path"]))
    key = lambda e: (e["item"], e["reason"])  # noqa: E731
    assert sorted(map(key, from_hwp["excluded"])) == sorted(map(key, from_docx["excluded"]))
    assert len(from_hwp["excluded"]) >= 1


@pytest.mark.fr("FR-35")
def test_preflight_and_review_map_work_on_hwp_with_same_rules(tmp_path: Path) -> None:
    pre_docx = _sync(tmp_path, "preflight", {"document": str(DOCX)})
    pre_hwp = _sync(tmp_path, "preflight", {"document": str(HWP)})
    assert "error" not in pre_hwp, pre_hwp
    summarize = lambda r: [(c["name"], c["status"]) for c in r["checks"]]  # noqa: E731
    assert summarize(pre_hwp) == summarize(pre_docx)

    map_docx = _sync(tmp_path, "review_map", {"document": str(DOCX)})
    map_hwp = _sync(tmp_path, "review_map", {"document": str(HWP)})
    assert "error" not in map_hwp, map_hwp
    reasons = lambda r: [(i["reason"], i["text"]) for i in r["items"]]  # noqa: E731
    assert reasons(map_hwp) == reasons(map_docx)


@pytest.mark.fr("FR-35")
def test_unreadable_hwp_returns_failed_step_and_reason_and_server_survives(tmp_path: Path) -> None:
    broken = tmp_path / "broken.hwp"
    broken.write_bytes(b"this is not a hwp file")
    for tool, args in (
        ("doc_to_task_table", {"input": str(broken)}),
        ("preflight", {"document": str(broken)}),
        ("review_map", {"document": str(broken)}),
    ):
        out = _sync(tmp_path, tool, args)
        assert out.get("error"), f"{tool}: 실패 이유가 있어야 한다"
        assert out.get("step"), f"{tool}: 실패 단계가 있어야 한다"
    # 서버는 계속 동작: 정상 파일은 여전히 처리된다.
    ok = _sync(tmp_path, "doc_to_task_table", {"input": str(DOCX)})
    assert "error" not in ok
