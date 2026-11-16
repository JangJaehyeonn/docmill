"""FR-29 채팅 패널과 에이전트 연결 인수 테스트 (초안).

필요한 샘플: tests/fixtures/ 의 sales_q3.xlsx, meeting_1111.docx (있음).
가정(구현 방법이 정해지면 맞춰 고친다):
- 채팅 패널: `<input>`/`<textarea>` 라벨 `메시지`, 버튼 `보내기`. 사용자 말풍선은 `data-role="user"`,
  도구 카드는 `data-role="tool-card"`(도구 이름 텍스트 포함), 에이전트 마지막 메시지는 `data-role="assistant"`,
  실패 안내는 `role=alert`.
- 에이전트 명령은 `DOCMILL_STUDIO_AGENT_CMD` 문자열이며, 프롬프트(열린 탭·활성 파일 경로 포함)를 stdin으로 받는다.
  (인자로 넘기는 구현이어도 가짜 에이전트는 stdin과 argv를 모두 파일에 남긴다.)
- 도구 결과(tool_result)의 텍스트가 JSON이고 `path` 키에 파일 경로가 있으면 그 파일을 탭으로 연다/다시 불러온다.
  다시 불러온 탭은 `data-reloaded` 카운터가 늘어난다고 가정한다 (`role=tab`의 속성).
- 가짜 에이전트는 stream-json: assistant(tool_use) → user(tool_result) → assistant(text) → result.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

FAKE_AGENT = textwrap.dedent(
    """
    import json, sys, time
    def _read_stdin(timeout=2.0):
        # 앱이 stdin을 닫지 않아도 멈추지 않게, 2초 동안 새 입력이 없으면 그만 읽는다.
        import os, select
        fd = sys.stdin.fileno()
        chunks = []
        while select.select([fd], [], [], timeout)[0]:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks).decode("utf-8", "replace")
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    stdin = _read_stdin()
    with open(cfg["dump"], "w", encoding="utf-8") as f:
        json.dump({"stdin": stdin, "argv": sys.argv[2:], "env": dict(__import__("os").environ)}, f)
    if cfg.get("sleep"):
        time.sleep(cfg["sleep"])
    if cfg.get("fail"):
        sys.stderr.write("가짜 에이전트 오류\\n")
        sys.exit(3)
    def emit(o):
        print(json.dumps(o, ensure_ascii=False), flush=True)
    emit({"type": "system", "subtype": "init"})
    for i, call in enumerate(cfg.get("calls", [])):
        emit({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": call["name"], "input": call.get("input", {})}]}})
        emit({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}",
             "content": [{"type": "text", "text": json.dumps(call["result"], ensure_ascii=False)}]}]}})
    emit({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": cfg.get("final", "완료했어요")}]}})
    emit({"type": "result", "subtype": "success", "result": cfg.get("final", "완료했어요")})
    """
)

_FR = "FR-29"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


def _agent(tmp_path: Path, **cfg: Any) -> tuple[dict[str, str], Path]:
    script = tmp_path / "fake_agent.py"
    script.write_text(FAKE_AGENT, encoding="utf-8")
    dump = tmp_path / "agent_input.json"
    conf = tmp_path / "agent_cfg.json"
    conf.write_text(json.dumps({"dump": str(dump), **cfg}), encoding="utf-8")
    cmd = f'"{sys.executable}" "{script}" "{conf}"'
    return {"DOCMILL_STUDIO_AGENT_CMD": cmd}, dump


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    for name in ("sales_q3.xlsx", "meeting_1111.docx"):
        shutil.copy(FIXTURES / name, ws / name)
    return ws


def _open(page: Any, name: str) -> None:
    page.get_by_role("listitem").filter(has_text=name).first.click()
    page.get_by_role("tab", name=name).wait_for()


def _send(page: Any, text: str) -> None:
    page.get_by_label("메시지").fill(text)
    page.get_by_role("button", name="보내기").click()


@pytest.mark.fr("FR-29")
def test_chat_shows_user_bubble_tool_card_and_final_message_in_order(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    env, _ = _agent(
        tmp_path,
        calls=[
            {
                "name": "mcp__docmill__verify_numbers",
                "input": {"document": "x"},
                "result": {"checked": 1, "matched": 1, "mismatches": []},
            }
        ],
        final="숫자가 모두 맞아요",
    )
    page.goto(start_studio(workspace=_ws(tmp_path), env=env))
    _send(page, "숫자 확인해줘")
    page.locator('[data-role="assistant"]').filter(has_text="숫자가 모두 맞아요").wait_for()
    order = page.locator('[data-role="user"], [data-role="tool-card"], [data-role="assistant"]')
    roles = [order.nth(i).get_attribute("data-role") for i in range(order.count())]
    assert roles == ["user", "tool-card", "assistant"]
    assert "숫자 확인해줘" in order.nth(0).inner_text()
    assert "verify_numbers" in order.nth(1).inner_text()


@pytest.mark.fr("FR-29")
def test_new_path_opens_new_tab_and_existing_path_reloads_tab(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = _ws(tmp_path)
    new_file = ws / "tasks.xlsx"
    shutil.copy(
        FIXTURES / "sales_q3.xlsx", new_file
    )  # 에이전트가 만든 새 파일 (이미 작업 폴더에 있음)
    env, _ = _agent(
        tmp_path,
        calls=[
            {"name": "mcp__docmill__doc_to_task_table", "result": {"path": str(new_file)}},
            {
                "name": "mcp__docmill__apply_template",
                "result": {"path": str(ws / "meeting_1111.docx")},
            },
        ],
    )
    page.goto(start_studio(workspace=ws, env=env))
    _open(page, "meeting_1111.docx")
    before = page.get_by_role("tab", name="meeting_1111.docx").get_attribute("data-reloaded") or "0"
    _send(page, "처리해줘")
    page.get_by_role("tab", name="tasks.xlsx").wait_for()
    page.wait_for_function(
        "([b]) => { const t = [...document.querySelectorAll('[role=tab]')]"
        ".find(e => e.textContent.includes('meeting_1111.docx'));"
        " return t && Number(t.getAttribute('data-reloaded') || 0) > Number(b); }",
        arg=[before],
    )
    assert page.get_by_role("tab").count() == 2


@pytest.mark.fr("FR-29")
def test_agent_input_contains_active_file_and_open_tabs(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = _ws(tmp_path)
    env, dump = _agent(tmp_path)
    page.goto(start_studio(workspace=ws, env=env))
    _open(page, "sales_q3.xlsx")
    _open(page, "meeting_1111.docx")
    _send(page, "안녕")
    page.locator('[data-role="assistant"]').wait_for()
    seen = json.loads(dump.read_text(encoding="utf-8"))
    blob = seen["stdin"] + " ".join(seen["argv"])
    assert str(ws / "meeting_1111.docx") in blob, "활성 파일 경로"
    assert str(ws / "sales_q3.xlsx") in blob, "열린 탭 목록"
    assert "안녕" in blob


@pytest.mark.fr("FR-29")
def test_agent_failure_and_timeout_are_shown_and_app_keeps_working(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = _ws(tmp_path)
    env, _ = _agent(tmp_path, fail=True)
    page.goto(start_studio(workspace=ws, env=env))
    _send(page, "실패 시켜줘")
    alert = page.get_by_role("alert")
    alert.wait_for()
    assert alert.inner_text().strip(), "실패 이유가 보여야 한다"
    _open(page, "sales_q3.xlsx")  # 앱은 계속 동작한다
    assert page.get_by_role("tab").count() == 1


@pytest.mark.fr("FR-29")
def test_agent_not_finishing_in_120_seconds_shows_failure(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    env, _ = _agent(tmp_path, sleep=300)
    page.goto(start_studio(workspace=_ws(tmp_path), env=env))
    _send(page, "오래 걸리는 일")
    page.get_by_role("alert").wait_for(timeout=150_000)
    assert "120" in page.get_by_role("alert").inner_text() or "시간" in (
        page.get_by_role("alert").inner_text()
    )
    _open(page, "sales_q3.xlsx")
    assert page.get_by_role("tab").count() == 1


@pytest.mark.fr("FR-29")
def test_anthropic_api_key_is_not_passed_to_agent(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    env, dump = _agent(tmp_path)
    env["ANTHROPIC_API_KEY"] = "sk-ant-test-not-real"
    # start_studio는 os.environ 위에 env를 덮어 서버에 넘기므로 서버는 키를 가진 채 뜬다.
    page.goto(start_studio(workspace=_ws(tmp_path), env=env))
    _send(page, "키 확인")
    page.locator('[data-role="assistant"]').wait_for()
    seen = json.loads(dump.read_text(encoding="utf-8"))
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert "sk-ant-test-not-real" not in json.dumps(seen)
