"""FR-30 열면 먼저 제안 인수 테스트 (초안).

필요한 샘플: tests/fixtures/ 의 meeting_1111.docx, sales_q3.xlsx (있음).
가정(구현 방법이 정해지면 맞춰 고친다):
- MCP 도구 `suggest_actions`: 인자 `input`(파일 경로). 결과 JSON에 `suggestions`(1~3개 목록)가 있고
  각 항목은 `label`(한국어 버튼 문구), `tool`(docmill 도구 이름), `message`(채팅으로 보낼 문구)를 가진다.
- studio: 파일을 열면 채팅 패널 맨 위에 제안 버튼(`role=button`, 이름은 label)이 보이고,
  누르면 `message`가 사용자 말풍선(`data-role="user"`)으로 보내진다.
- 가짜 에이전트는 받은 입력을 파일로 남긴다 (stdin 전체).
"""

# ruff: noqa: E501, F811, F401

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.acceptance.conftest import page, start_studio

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

_FR = "FR-30"
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


def _suggest(home: Path, sample: str) -> dict[str, Any]:
    args = {"input": str(FIXTURES / sample)}
    return asyncio.run(asyncio.wait_for(_call(home, "suggest_actions", args), 90))


@pytest.mark.fr("FR-30")
def test_docx_meeting_suggests_task_table(tmp_path: Path) -> None:
    items = _suggest(tmp_path, "meeting_1111.docx")["suggestions"]
    assert 1 <= len(items) <= 3
    assert any(s["tool"] == "doc_to_task_table" and "할 일 표 만들기" in s["label"] for s in items)
    assert all(s["label"] and s["message"] for s in items)


@pytest.mark.fr("FR-30")
def test_xlsx_suggests_report_ppt(tmp_path: Path) -> None:
    items = _suggest(tmp_path, "sales_q3.xlsx")["suggestions"]
    assert 1 <= len(items) <= 3
    assert any(
        s["tool"] == "excel_to_report_ppt" and "보고서 PPT 만들기" in s["label"] for s in items
    )


@pytest.mark.fr("FR-30")
def test_same_file_gives_same_suggestions_in_same_order(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert _suggest(a, "meeting_1111.docx") == _suggest(b, "meeting_1111.docx")


_FAKE = textwrap.dedent(
    """
    import json, sys
    cfg = sys.argv[1]
    open(cfg, "w", encoding="utf-8").write(sys.stdin.read())
    for o in ({"type": "assistant", "message": {"content": [{"type": "text", "text": "알겠어요"}]}},
              {"type": "result", "subtype": "success", "result": "알겠어요"}):
        print(json.dumps(o, ensure_ascii=False), flush=True)
    """
)


@pytest.mark.fr("FR-30")
def test_studio_shows_suggestion_buttons_and_sends_message(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    shutil.copy(FIXTURES / "meeting_1111.docx", ws / "meeting_1111.docx")
    script = tmp_path / "fake.py"
    script.write_text(_FAKE, encoding="utf-8")
    dump = tmp_path / "stdin.txt"
    env = {"DOCMILL_STUDIO_AGENT_CMD": f'"{sys.executable}" "{script}" "{dump}"'}
    page.goto(start_studio(workspace=ws, env=env))
    page.get_by_role("listitem").filter(has_text="meeting_1111.docx").first.click()
    button = page.get_by_role("button", name="할 일 표 만들기")
    button.wait_for(timeout=60_000)
    # 제안 문구는 도구 결과의 message와 같아야 한다.
    expected = _suggest(tmp_path, "meeting_1111.docx")["suggestions"]
    message = next(s["message"] for s in expected if s["tool"] == "doc_to_task_table")
    button.click()
    bubble = page.locator('[data-role="user"]').first
    bubble.wait_for()
    assert message in bubble.inner_text()
    page.locator('[data-role="assistant"]').wait_for()
    assert message in dump.read_text(encoding="utf-8")
