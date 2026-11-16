"""FR-20 결과 미리보기 이미지 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/sales_q3.xlsx (있음): 첫 행 머리글 `부서·월·매출`
가정(구현 방법이 정해지면 맞춰 고친다):
- 도구 결과는 JSON 텍스트이며 첫 슬라이드 PNG 경로가 `preview` 키에 들어간다.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SAMPLE = Path(__file__).resolve().parents[2] / "fixtures" / "sales_q3.xlsx"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


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


@pytest.mark.fr("FR-20")
def test_result_includes_first_slide_png_path(tmp_path: Path) -> None:
    args = {
        "input": str(SAMPLE),
        "group_by": "부서",
        "value": "매출",
        "slides": 5,
        "chart": "bar",
    }
    out = asyncio.run(asyncio.wait_for(_call(tmp_path, "excel_to_report_ppt", args), 120))
    assert "error" not in out, out
    preview = Path(out["preview"])
    assert preview.suffix == ".png" and preview.exists()
    assert preview.read_bytes()[:8] == PNG_MAGIC
