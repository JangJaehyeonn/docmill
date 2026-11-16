"""FR-32 미리 보고 적용 인수 테스트 (초안).

필요한 샘플: tests/fixtures/ 의 meeting_1111.docx (있음). 변경된 사본은 테스트가 pptx 대신
meeting_1111.docx에 문구를 덧붙여 만든다 (표준 라이브러리 zipfile 사용).
가정(구현 방법이 정해지면 맞춰 고친다):
- 가짜 에이전트는 작업 폴더의 파일을 직접 고치는 척하지 않고, 도구 결과로
  `{"path": <원본 경로>, "modified_copy": <사본 경로>}` 를 낸다. studio는 `modified_copy`가 있으면
  원본을 건드리지 않고 미리보기 카드(`data-role="preview-card"`)를 보여준다.
  카드에는 요약 문구와 `적용`·`취소` 버튼이 있다. 사본은 작업 폴더 안에 `.docmill-preview` 접미사 폴더/파일로 둔다고
  가정하되, 테스트는 "취소 후 작업 폴더에 사본 경로의 파일이 없다"만 본다.
- 적용 후 탭 `data-reloaded` 값이 늘어난다.
"""

# ruff: noqa: E501, F811, F401

from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.acceptance.conftest import page, start_studio

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
NAME = "meeting_1111.docx"
MARK = "미리보기-표식"

_FAKE = textwrap.dedent(
    """
    import json, sys
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    sys.stdin.read()
    def emit(o): print(json.dumps(o, ensure_ascii=False), flush=True)
    emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t0", "name": "mcp__docmill__guarded_edit", "input": {}}]}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t0",
        "content": [{"type": "text", "text": json.dumps(cfg["result"], ensure_ascii=False)}]}]}})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "고쳤어요"}]}})
    emit({"type": "result", "subtype": "success", "result": "고쳤어요"})
    """
)

_FR = "FR-32"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


def _make_copy(src: Path, dst: Path) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                data = data.replace(
                    b"</w:body>", f"<w:p><w:r><w:t>{MARK}</w:t></w:r></w:p></w:body>".encode(), 1
                )
            zout.writestr(item, data)


def _doc_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read("word/document.xml").decode("utf-8")


def _setup(tmp_path: Path, start_studio: Callable[..., str], page: Any) -> tuple[Path, Path, Path]:
    ws = tmp_path / "ws"
    ws.mkdir()
    original = ws / NAME
    shutil.copy(FIXTURES / NAME, original)
    copy_dir = ws / ".docmill-preview"
    copy_dir.mkdir()
    copy = copy_dir / NAME
    _make_copy(FIXTURES / NAME, copy)
    script = tmp_path / "fake.py"
    script.write_text(_FAKE, encoding="utf-8")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(
        json.dumps(
            {"result": {"path": str(original), "modified_copy": str(copy), "changed_paragraphs": 1}}
        ),
        encoding="utf-8",
    )
    env = {"DOCMILL_STUDIO_AGENT_CMD": f'"{sys.executable}" "{script}" "{cfg}"'}
    page.goto(start_studio(workspace=ws, env=env))
    page.get_by_role("listitem").filter(has_text=NAME).first.click()
    page.get_by_role("tab", name=NAME).wait_for()
    page.get_by_label("메시지").fill("한 줄 덧붙여줘")
    page.get_by_role("button", name="보내기").click()
    page.locator('[data-role="preview-card"]').wait_for()
    return ws, original, copy


@pytest.mark.fr("FR-32")
def test_preview_card_shown_and_original_untouched(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    _, original, _ = _setup(tmp_path, start_studio, page)
    card = page.locator('[data-role="preview-card"]')
    assert card.get_by_role("button", name="적용").is_visible()
    assert card.get_by_role("button", name="취소").is_visible()
    assert original.read_bytes() == (FIXTURES / NAME).read_bytes()


@pytest.mark.fr("FR-32")
def test_apply_replaces_original_and_reloads_tab(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    _, original, _ = _setup(tmp_path, start_studio, page)
    tab = page.get_by_role("tab", name=NAME)
    before = tab.get_attribute("data-reloaded") or "0"
    page.locator('[data-role="preview-card"]').get_by_role("button", name="적용").click()
    page.wait_for_function(
        "([b, n]) => { const t = [...document.querySelectorAll('[role=tab]')]"
        ".find(e => e.textContent.includes(n));"
        " return t && Number(t.getAttribute('data-reloaded') || 0) > Number(b); }",
        arg=[before, NAME],
    )
    assert MARK in _doc_text(original)


@pytest.mark.fr("FR-32")
def test_cancel_keeps_original_and_removes_copy(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws, original, copy = _setup(tmp_path, start_studio, page)
    page.locator('[data-role="preview-card"]').get_by_role("button", name="취소").click()
    page.locator('[data-role="preview-card"]').wait_for(state="detached")
    assert original.read_bytes() == (FIXTURES / NAME).read_bytes()
    assert not copy.exists()
    leftovers = [p for p in ws.rglob("*") if p.is_file() and p != original]
    assert not leftovers, f"사본이 남아 있다: {leftovers}"
