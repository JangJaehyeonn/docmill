"""FR-45 나를 기억 (부서·결재 라인·양식) 인수 테스트 (초안).

필요한 샘플: tests/fixtures/meeting_1111.docx (있음). 직함 점검용 docx는 테스트가 zipfile로 만든다.
가정(구현 방법이 정해지면 맞춰 고친다):
- 시작 화면에 `설정` 버튼이 있고, 누르면 `<label>` 부서·이름·직함·결재 라인·기본 양식·문체 입력과
  `저장` 버튼이 나온다. 결재 라인은 `기안자 > 팀장 > 부장`처럼 `>` 로 구분한 한 줄이다.
- 저장하면 작업 폴더에 `.docmill/settings.json`이 생긴다.
  키: `department`, `name`, `title`, `approval_line`(목록), `default_template`, `tone`.
- 에이전트 입력(stdin 또는 argv)에 부서와 결재 라인이 들어간다 (가짜 에이전트가 파일로 남긴다).
- preflight 도구는 선택 인자 `workspace`(설정이 있는 작업 폴더)를 받는다. 결과 `checks`에
  `직함` 항목이 있고, 문서의 `직함 이름` 서명이 설정 직함과 다르면 `주의`, 같으면 `통과`다.
- 이 파일은 tests/acceptance/ 로 옮겨졌을 때 conftest의 start_studio·page 픽스처를 쓴다.
"""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import textwrap
import time
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
DEPT = "영업기획팀"
NAME = "김민수"
TITLE = "대리"
LINE = "기안자 > 팀장 > 부장"
TEMPLATE = "주간 보고 양식"
TONE = "공문서체"

FAKE_AGENT = textwrap.dedent(
    """
    import json, os, select, sys
    fd = sys.stdin.fileno()
    chunks = []
    while select.select([fd], [], [], 2.0)[0]:
        b = os.read(fd, 65536)
        if not b:
            break
        chunks.append(b)
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    json.dump({"stdin": b"".join(chunks).decode("utf-8", "replace"), "argv": sys.argv[2:]},
              open(cfg["dump"], "w", encoding="utf-8"))
    def emit(o):
        print(json.dumps(o, ensure_ascii=False), flush=True)
    emit({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "알겠어요"}]}})
    emit({"type": "result", "subtype": "success", "result": "알겠어요"})
    """
)

_FR = "FR-45"
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


def _make_docx(path: Path, paragraphs: list[str]) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return path


def _save_settings(page: Any, ws: Path) -> dict[str, Any]:
    page.get_by_role("button", name="설정", exact=True).click()
    page.get_by_label("부서", exact=True).fill(DEPT)
    page.get_by_label("이름", exact=True).fill(NAME)
    page.get_by_label("직함", exact=True).fill(TITLE)
    page.get_by_label("결재 라인", exact=True).fill(LINE)
    page.get_by_label("기본 양식", exact=True).fill(TEMPLATE)
    page.get_by_label("문체", exact=True).fill(TONE)
    page.get_by_role("button", name="저장", exact=True).click()
    path = ws / ".docmill" / "settings.json"
    end = time.monotonic() + 30
    while time.monotonic() < end:
        try:
            saved = dict(json.loads(path.read_text(encoding="utf-8")))
            if saved.get("tone") == TONE:
                return saved
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise AssertionError("설정 파일이 저장돼야 한다")


@pytest.mark.fr("FR-45")
def test_settings_survive_restart(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    page.goto(start_studio(workspace=ws))
    saved = _save_settings(page, ws)
    assert saved["department"] == DEPT and saved["name"] == NAME and saved["title"] == TITLE
    assert saved["approval_line"] == ["기안자", "팀장", "부장"]
    assert saved["default_template"] == TEMPLATE and saved["tone"] == TONE
    # 앱을 다시 띄운다 (같은 작업 폴더, 새 서버).
    page.goto(start_studio(workspace=ws))
    page.get_by_role("button", name="설정", exact=True).click()
    assert page.get_by_label("부서", exact=True).input_value() == DEPT
    assert page.get_by_label("이름", exact=True).input_value() == NAME
    assert page.get_by_label("직함", exact=True).input_value() == TITLE
    assert page.get_by_label("결재 라인", exact=True).input_value() == LINE
    assert page.get_by_label("기본 양식", exact=True).input_value() == TEMPLATE
    assert page.get_by_label("문체", exact=True).input_value() == TONE


@pytest.mark.fr("FR-45")
def test_agent_input_contains_saved_department_and_approval_line(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    shutil.copy(FIXTURES / "meeting_1111.docx", ws / "meeting_1111.docx")
    script = tmp_path / "fake_agent.py"
    script.write_text(FAKE_AGENT, encoding="utf-8")
    dump = tmp_path / "agent_input.json"
    conf = tmp_path / "cfg.json"
    conf.write_text(json.dumps({"dump": str(dump)}), encoding="utf-8")
    env = {"DOCMILL_STUDIO_AGENT_CMD": f'"{sys.executable}" "{script}" "{conf}"'}
    page.goto(start_studio(workspace=ws, env=env))
    _save_settings(page, ws)
    page.get_by_label("메시지").fill("안녕")
    page.get_by_role("button", name="보내기", exact=True).click()
    page.locator('[data-role="assistant"]').first.wait_for(timeout=60_000)
    seen = json.loads(dump.read_text(encoding="utf-8"))
    blob = seen["stdin"] + " ".join(seen["argv"])
    assert DEPT in blob
    assert "팀장" in blob and "부장" in blob


@pytest.mark.fr("FR-45")
def test_preflight_warns_when_document_title_differs_from_settings(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / ".docmill").mkdir(parents=True)
    settings = {
        "department": DEPT,
        "name": NAME,
        "title": TITLE,
        "approval_line": ["기안자", "팀장", "부장"],
        "default_template": "",
        "tone": TONE,
    }
    (ws / ".docmill" / "settings.json").write_text(
        json.dumps(settings, ensure_ascii=False), encoding="utf-8"
    )
    wrong = _make_docx(ws / "wrong.docx", ["주간 보고", f"기안자: 과장 {NAME}"])
    right = _make_docx(ws / "right.docx", ["주간 보고", f"기안자: {TITLE} {NAME}"])
    for doc, status in ((wrong, "주의"), (right, "통과")):
        out = _sync(tmp_path, "preflight", {"document": str(doc), "workspace": str(ws)})
        checks = {c["name"]: c for c in out["checks"]}
        assert checks["직함"]["status"] == status, (doc.name, checks["직함"])
        if status == "주의":
            assert checks["직함"]["locations"], "위치가 나와야 한다"
