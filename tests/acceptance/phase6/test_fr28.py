"""FR-28 한글 편집 화면 (rhwp) 인수 테스트 (초안).

필요한 샘플: tests/fixtures/meeting_1111.hwp (있음, 문단 11개).
환경: RHWP_STUDIO_DIST (rhwp 편집기 빌드 폴더. 없으면 실패).
가정(구현 방법이 정해지면 맞춰 고친다):
- hwp 탭 패널(`role=tabpanel`)에 rhwp 편집기 iframe(`/rhwp/`)이 뜨고, 첫 쪽이 그려지면
  패널에 `data-editor-state="ready"`가 붙고 쪽 수가 `쪽 수: N` 텍스트(예: "1쪽")로 보인다.
- 페이지에 `window.rhwpEditor`(@rhwp/editor 인스턴스)가 노출돼 `insertText(text)`·`getText()` 호출이 가능하다.
- 저장은 `저장` 버튼. 다시 열기는 탭을 닫고(`탭 닫기` 버튼) 목록에서 다시 연다.
"""

# ruff: noqa: E501

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
NAME = "meeting_1111.hwp"

_FR = "FR-28"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


def _env() -> dict[str, str]:
    dist = os.environ.get("RHWP_STUDIO_DIST")
    if not dist:
        pytest.fail("RHWP_STUDIO_DIST가 필요합니다 (rhwp 편집기 셀프 빌드 폴더).")
    return {"RHWP_STUDIO_DIST": dist}


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    shutil.copy(FIXTURES / NAME, ws / NAME)
    return ws


def _open(page: Any) -> None:
    page.get_by_role("listitem").filter(has_text=NAME).first.click()
    page.get_by_role("tab", name=NAME).wait_for()
    page.locator('[role="tabpanel"][data-editor-state="ready"]').wait_for(timeout=60_000)


@pytest.mark.fr("FR-28")
def test_hwp_first_page_rendered_with_page_count(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    url = start_studio(workspace=_ws(tmp_path), env=_env())
    page.goto(url)
    _open(page)
    assert page.get_by_text("쪽 수").first.is_visible()
    assert page.locator("iframe").count() >= 1


@pytest.mark.fr("FR-28")
def test_insert_text_save_and_reopen_shows_text(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = _ws(tmp_path)
    before = (ws / NAME).read_bytes()
    url = start_studio(workspace=ws, env=_env())
    page.goto(url)
    _open(page)
    marker = "도큐밀-FR28-표식"
    page.evaluate("(t) => window.rhwpEditor.insertText(t)", marker)
    page.get_by_role("button", name="저장").click()
    page.wait_for_function("() => document.body.innerText.includes('저장했어요')", timeout=60_000)
    assert (ws / NAME).read_bytes() != before, "작업 폴더의 hwp가 바뀌어야 한다"

    page.get_by_role("button", name="탭 닫기").first.click()
    _open(page)
    text = page.evaluate("() => window.rhwpEditor.getText()")
    assert marker in text


@pytest.mark.fr("FR-28")
def test_rhwp_resources_come_only_from_studio_server(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    url = start_studio(workspace=_ws(tmp_path), env=_env())
    host = urlparse(url).netloc
    requested: list[str] = []
    page.on("request", lambda req: requested.append(req.url))
    page.goto(url)
    _open(page)
    external = [u for u in requested if u.startswith("http") and urlparse(u).netloc != host]
    assert not external, f"studio 서버 밖에서 불러온 자원: {external}"
    assert any("/rhwp/" in u for u in requested), "rhwp 자원은 /rhwp/ 에서 와야 한다"
    assert not any("edwardkim.github.io" in u for u in requested)
