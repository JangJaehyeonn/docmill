"""FR-36 여러 파일 넘나들기 인수 테스트 (초안).

필요한 샘플: tests/fixtures/ 의 meeting_1111.docx, sales_q3.xlsx, company_intro.pptx (있음).
가정(구현 방법이 정해지면 맞춰 고친다):
- FR-29와 같은 채팅 UI·가짜 에이전트 계약(프롬프트는 stdin, 도구 결과 JSON의 `path`로 탭 열기)을 쓴다.
- 가짜 에이전트는 받은 입력을 파일로 남긴다.
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

_FAKE = textwrap.dedent(
    """
    import json, sys
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
    open(cfg["dump"], "w", encoding="utf-8").write(_read_stdin() + " ".join(sys.argv[2:]))
    def emit(o): print(json.dumps(o, ensure_ascii=False), flush=True)
    for i, call in enumerate(cfg["calls"]):
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": call["name"], "input": {}}]}})
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}",
            "content": [{"type": "text", "text": json.dumps(call["result"], ensure_ascii=False)}]}]}})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "다 만들었어요"}]}})
    emit({"type": "result", "subtype": "success", "result": "다 만들었어요"})
    """
)

_FR = "FR-36"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


def _setup(tmp_path: Path, calls: list[dict[str, Any]]) -> tuple[Path, dict[str, str], Path]:
    ws = tmp_path / "ws"
    ws.mkdir()
    for name in ("meeting_1111.docx", "sales_q3.xlsx", "company_intro.pptx"):
        shutil.copy(FIXTURES / name, ws / name)
    script = tmp_path / "fake.py"
    script.write_text(_FAKE, encoding="utf-8")
    dump = tmp_path / "input.txt"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"dump": str(dump), "calls": calls}), encoding="utf-8")
    return ws, {"DOCMILL_STUDIO_AGENT_CMD": f'"{sys.executable}" "{script}" "{cfg}"'}, dump


def _open(page: Any, name: str) -> None:
    page.get_by_role("listitem").filter(has_text=name).first.click()
    page.get_by_role("tab", name=name).wait_for()


@pytest.mark.fr("FR-36")
def test_agent_input_lists_names_and_paths_of_all_open_tabs(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws, env, dump = _setup(tmp_path, [])
    page.goto(start_studio(workspace=ws, env=env))
    names = ["meeting_1111.docx", "sales_q3.xlsx", "company_intro.pptx"]
    for name in names:
        _open(page, name)
    page.get_by_label("메시지").fill("할일 표로 주간 보고 만들어줘")
    page.get_by_role("button", name="보내기").click()
    page.locator('[data-role="assistant"]').wait_for()
    seen = dump.read_text(encoding="utf-8")
    for name in names:
        assert name in seen
        assert str(ws / name) in seen


@pytest.mark.fr("FR-36")
def test_chained_tool_calls_open_two_new_tabs_and_last_is_active(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = tmp_path / "ws"
    # 두 단계의 결과 파일은 작업 폴더에 이미 있는 것으로 둔다 (회의록 → 할 일 xlsx → 보고 pptx).
    ws_probe, env, _ = _setup(
        tmp_path,
        [
            {"name": "mcp__docmill__doc_to_task_table", "result": {"path": str(ws / "tasks.xlsx")}},
            {
                "name": "mcp__docmill__excel_to_report_ppt",
                "result": {"path": str(ws / "weekly.pptx")},
            },
        ],
    )
    assert ws == ws_probe
    shutil.copy(FIXTURES / "sales_q3.xlsx", ws / "tasks.xlsx")
    shutil.copy(FIXTURES / "company_intro.pptx", ws / "weekly.pptx")
    page.goto(start_studio(workspace=ws, env=env))
    _open(page, "meeting_1111.docx")
    page.get_by_label("메시지").fill("회의록으로 할일 표 만들고 주간 보고 만들어줘")
    page.get_by_role("button", name="보내기").click()
    page.get_by_role("tab", name="weekly.pptx").wait_for()
    page.get_by_role("tab", name="tasks.xlsx").wait_for()
    assert page.get_by_role("tab").count() == 3
    assert page.get_by_role("tab", name="weekly.pptx").get_attribute("aria-selected") == "true"
    assert page.get_by_role("tab", name="tasks.xlsx").get_attribute("aria-selected") == "false"
