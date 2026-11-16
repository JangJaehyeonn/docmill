"""FR-34 보내기 전 안전 점검 (preflight) 인수 테스트 (초안).

필요한 샘플: 테스트가 docx를 직접 만든다 (표준 라이브러리 zipfile, 최소 docx). 고정 샘플은
tests/fixtures/ 의 template_word.docx 처럼 문제 없는 파일이 필요하면 FIXTURES.md를 보라
(여기서는 테스트가 만드는 '깨끗한 docx'를 쓴다).
가정(구현 방법이 정해지면 맞춰 고친다):
- MCP 도구 `preflight` 인자: `document`, 선택 `source`·`group_by`·`value`(숫자 검증용).
- 결과 JSON: `checks` 목록. 각 항목은 `name`(`숫자 검증`·`날짜 요일`·`개인정보`·`자리표시자`·`빈 제목`),
  `status`(`통과`·`주의`·`실패`), `locations`(목록; 각 항목은 문자열 또는 {location, text}).
  원본 파일이 없으면 `숫자 검증`은 `통과`(건너뜀)로 둔다.
- studio: 파일 탭이 열린 상태에서 `보내기 전 점검` 버튼을 누르면 채팅에 항목 이름과 상태가 보인다.
  이때 에이전트 없이 도구가 바로 호출된다.
"""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import os
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

RRN = "900101-1234567"
CHECKS = ["숫자 검증", "날짜 요일", "개인정보", "자리표시자", "빈 제목"]

_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)

_FR = "FR-34"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


def _make_docx(path: Path, paragraphs: list[str]) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", doc)
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


def _preflight(home: Path, doc: Path) -> dict[str, dict[str, Any]]:
    out = asyncio.run(asyncio.wait_for(_call(home, "preflight", {"document": str(doc)}), 90))
    return {c["name"]: c for c in out["checks"]}


CLEAN = ["주간 보고", "2026. 11. 16.(월) 회의를 진행했다.", "다음 회의는 2026. 11. 17.(화)에 한다."]


@pytest.mark.fr("FR-34")
def test_weekday_mismatch_fails_with_location(tmp_path: Path) -> None:
    doc = _make_docx(tmp_path / "a.docx", ["주간 보고", "2026. 11. 16.(화) 회의를 진행했다."])
    check = _preflight(tmp_path, doc)["날짜 요일"]
    assert check["status"] == "실패"
    assert check["locations"], "위치가 나와야 한다"
    assert "2026. 11. 16." in json.dumps(check["locations"], ensure_ascii=False)


@pytest.mark.fr("FR-34")
def test_resident_number_fails_and_is_not_echoed(tmp_path: Path) -> None:
    doc = _make_docx(tmp_path / "b.docx", ["주간 보고", f"담당자 주민등록번호 {RRN}"])
    out = asyncio.run(asyncio.wait_for(_call(tmp_path, "preflight", {"document": str(doc)}), 90))
    checks = {c["name"]: c for c in out["checks"]}
    assert checks["개인정보"]["status"] == "실패"
    assert checks["개인정보"]["locations"]
    dumped = json.dumps(out, ensure_ascii=False)
    assert RRN not in dumped and "1234567" not in dumped, "원래 번호를 그대로 쓰면 안 된다"


@pytest.mark.fr("FR-34")
def test_placeholders_give_warning(tmp_path: Path) -> None:
    doc = _make_docx(tmp_path / "c.docx", ["주간 보고", "담당: ○○○", "일정: TBD"])
    assert _preflight(tmp_path, doc)["자리표시자"]["status"] == "주의"


@pytest.mark.fr("FR-34")
def test_clean_sample_passes_every_check(tmp_path: Path) -> None:
    doc = _make_docx(tmp_path / "d.docx", CLEAN)
    checks = _preflight(tmp_path, doc)
    assert set(CHECKS) <= set(checks)
    assert all(checks[n]["status"] == "통과" for n in CHECKS), checks


@pytest.mark.fr("FR-34")
def test_studio_preflight_button_shows_results_in_chat(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _make_docx(ws / "report.docx", ["주간 보고", "담당: ○○○"])
    page.goto(start_studio(workspace=ws))
    page.get_by_role("listitem").filter(has_text="report.docx").first.click()
    page.get_by_role("tab", name="report.docx").wait_for()
    page.get_by_role("button", name="보내기 전 점검").click()
    chat = page.locator('[data-role="tool-card"], [data-role="assistant"]')
    chat.first.wait_for(timeout=60_000)
    # 버튼 이름 등 화면 다른 곳의 글자와 섞이지 않게 채팅 카드 안의 글자만 본다.
    text = "\n".join(chat.nth(i).inner_text() for i in range(chat.count()))
    for name in CHECKS:
        assert name in text, name
    assert "주의" in text
