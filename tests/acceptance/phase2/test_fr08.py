"""FR-08 verify_numbers 업무 도구 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_q3.xlsx (있음)
- 올바른 결과 pptx는 FR-05 `excel_to_report_ppt`로 만든다.
  FR-05가 만든 pptx의 숫자 하나를 바꾼 "틀린 문서"는 테스트 안에서 zip을 고쳐 만든다.
가정(구현 방법이 정해지면 맞춰 고친다):
- 시트 첫 행이 머리글이고, 그룹 열 `GROUP_BY`와 값 열 `VALUE`가 있다.
- 도구 인자: `document`(결과 문서), `source`(원본 엑셀), `group_by`, `value`.
- 도구 결과는 JSON 텍스트이며 `checked`(대조 개수), `matched`(일치 개수),
  `mismatches`(목록; 각 항목에 `location`·`expected`·`actual`)를 가진다.
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
GROUP_BY = "부서"
VALUE = "매출"


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


def _call_sync(home: Path, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(asyncio.wait_for(_call(home, name, args), 90))


def _make_report(home: Path) -> Path:
    out = _call_sync(
        home,
        "excel_to_report_ppt",
        {
            "input": str(FIXTURES / "sales_q3.xlsx"),
            "group_by": GROUP_BY,
            "value": VALUE,
            "slides": 5,
            "chart": "bar",
            "verify": False,
        },
    )
    return Path(out["path"])


def _tamper_first_number(src: Path, dst: Path) -> str:
    """차트 캐시의 첫 숫자 값에 1을 더한 복사본을 만들고, 바뀐 원래 값을 돌려준다."""
    original = ""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if not original and re.fullmatch(r"ppt/charts/chart\d+\.xml", item.filename):
                text = data.decode("utf-8")
                m = re.search(r"<c:val>.*?<c:v>([^<]+)</c:v>", text, re.S)
                if m:
                    original = m.group(1)
                    changed = str(float(original) + 1)
                    text = text[: m.start(1)] + changed + text[m.end(1) :]
                    data = text.encode("utf-8")
            zout.writestr(item, data)
    assert original, "샘플 결과에서 그래프 숫자를 찾지 못했다"
    return original


@pytest.mark.fr("FR-08")
def test_correct_document_all_numbers_match(tmp_path: Path) -> None:
    report = _make_report(tmp_path)
    out = _call_sync(
        tmp_path,
        "verify_numbers",
        {
            "document": str(report),
            "source": str(FIXTURES / "sales_q3.xlsx"),
            "group_by": GROUP_BY,
            "value": VALUE,
        },
    )
    assert out["checked"] > 0
    assert out["matched"] == out["checked"]
    assert not out["mismatches"]


@pytest.mark.fr("FR-08")
def test_wrong_number_is_reported_with_location_and_expected(tmp_path: Path) -> None:
    report = _make_report(tmp_path)
    broken = tmp_path / "broken.pptx"
    original = _tamper_first_number(report, broken)
    out = _call_sync(
        tmp_path,
        "verify_numbers",
        {
            "document": str(broken),
            "source": str(FIXTURES / "sales_q3.xlsx"),
            "group_by": GROUP_BY,
            "value": VALUE,
        },
    )
    assert out["matched"] < out["checked"]
    assert len(out["mismatches"]) >= 1
    first = out["mismatches"][0]
    assert first["location"], "불일치 위치가 보고되어야 한다"
    assert float(first["expected"]) == pytest.approx(float(original)), "기대값은 원본 합계여야 한다"
