"""FR-18 양식 등록 인수 테스트 (초안).

필요한 샘플:
- tests/fixtures/template_default.pptx (있음): 샘플 양식 PPT (제목 40pt·`1F4E79`·가운데·Pretendard)
- tests/fixtures/company_intro.pptx (있음): 양식을 적용할 PPT
가정(구현 방법이 정해지면 맞춰 고친다):
- `docmill.templates.register_template(name, source, home=...)`이 양식 PPT에서 규칙을 추출해
  이름으로 저장하고, 추출한 규칙(제목 크기·색·정렬·글꼴)을 담은 dict를 돌려준다.
- `docmill.templates.list_templates(home=...)`가 저장된 양식 이름 목록을 돌려준다.
- apply_template 도구는 인자 `input`·`template`을 받고 JSON 결과에 `path`·`rules`를 가진다.
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

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
NAME = "회사양식"


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


@pytest.mark.fr("FR-18")
def test_extracted_rules_are_saved_and_listed(tmp_path: Path) -> None:
    from docmill import templates

    rules = templates.register_template(NAME, FIXTURES / "template_default.pptx", home=tmp_path)
    for key in ("title_size", "title_color", "title_align", "font"):
        assert rules.get(key), f"추출한 규칙에 {key}가 있어야 한다"
    assert NAME in templates.list_templates(home=tmp_path)


@pytest.mark.fr("FR-18")
def test_registered_template_usable_by_name_in_apply_template(tmp_path: Path) -> None:
    from docmill import templates

    templates.register_template(NAME, FIXTURES / "template_default.pptx", home=tmp_path)
    args = {"input": str(FIXTURES / "company_intro.pptx"), "template": NAME}
    out = asyncio.run(asyncio.wait_for(_call(tmp_path, "apply_template", args), 90))
    assert "error" not in out
    assert Path(out["path"]).exists()
    assert out["rules"]
