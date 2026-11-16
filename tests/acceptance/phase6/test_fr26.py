"""FR-26 studio 앱 골격과 파일 열기 인수 테스트 (초안).

필요한 샘플: tests/fixtures/ 의 sales_q3.xlsx, company_intro.pptx, meeting_1111.docx, meeting_1111.hwp (모두 있음).
가정(구현 방법이 정해지면 맞춰 고친다):
- 시작 화면에 `파일 열기` 버튼과 작업 폴더 파일 목록(`role=list` 안의 `listitem`, 파일 이름 텍스트)이 있다.
- 목록 항목을 누르면(또는 `파일 열기` 후 선택) 파일이 열리고 위쪽 탭은 `role=tab`, 이름은 파일 이름이다.
- 활성 탭은 `aria-selected="true"`.
- 최근 파일 목록은 시작 화면의 `region` "최근 파일" 안의 `listitem`이다. 가장 최근이 맨 위다.
- 작업 폴더 밖 경로는 `?open=<경로>` 쿼리로 열 수 있고, 거부 안내(`role=alert`)에 '작업 폴더' 문구가 있다.
- 이 파일은 tests/acceptance/ 로 옮겨졌을 때 conftest의 start_studio·page 픽스처를 쓴다.
"""

# ruff: noqa: E501, F811, F401

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from tests.acceptance.conftest import page, start_studio

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "fixtures"
NAMES = ["sales_q3.xlsx", "company_intro.pptx", "meeting_1111.docx", "meeting_1111.hwp"]

_FR = "FR-26"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    for name in NAMES:
        shutil.copy(FIXTURES / name, ws / name)
    return ws


def _open_file(page: Any, name: str) -> None:
    page.get_by_role("listitem").filter(has_text=name).first.click()


@pytest.mark.fr("FR-26")
def test_start_screen_shows_open_button_and_workspace_files(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    url = start_studio(workspace=_workspace(tmp_path))
    page.goto(url)
    assert page.get_by_role("button", name="파일 열기").is_visible()
    for name in NAMES:
        assert page.get_by_text(name).first.is_visible(), name


@pytest.mark.fr("FR-26")
def test_opening_two_files_creates_two_tabs_and_switches_active(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    url = start_studio(workspace=_workspace(tmp_path))
    page.goto(url)
    _open_file(page, "sales_q3.xlsx")
    page.get_by_role("tab", name="sales_q3.xlsx").wait_for()
    _open_file(page, "meeting_1111.docx")
    page.get_by_role("tab", name="meeting_1111.docx").wait_for()
    assert page.get_by_role("tab").count() == 2
    page.get_by_role("tab", name="sales_q3.xlsx").click()
    assert page.get_by_role("tab", name="sales_q3.xlsx").get_attribute("aria-selected") == "true"
    assert page.get_by_role("tab", name="meeting_1111.docx").get_attribute("aria-selected") == (
        "false"
    )


@pytest.mark.fr("FR-26")
def test_recent_file_is_on_top_after_restart(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = _workspace(tmp_path)
    env = {"DOCMILL_HOME": str(tmp_path / "home"), "HOME": str(tmp_path / "home")}
    url = start_studio(workspace=ws, env=env)
    page.goto(url)
    _open_file(page, "company_intro.pptx")
    page.get_by_role("tab", name="company_intro.pptx").wait_for()

    # 두 번째 서버(같은 작업 폴더·같은 홈)를 띄워 새로 연다.
    url2 = start_studio(workspace=ws, env=env)
    page.goto(url2)
    recent = page.get_by_role("region", name="최근 파일").get_by_role("listitem")
    recent.first.wait_for()
    assert "company_intro.pptx" in recent.first.inner_text()


@pytest.mark.fr("FR-26")
def test_path_outside_workspace_is_rejected_with_reason(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = _workspace(tmp_path)
    (tmp_path / "secret.docx").write_bytes(b"x")
    url = start_studio(workspace=ws)
    page.goto(f"{url.rstrip('/')}/?open={quote('../secret.docx')}")
    alert = page.get_by_role("alert")
    alert.wait_for()
    assert "작업 폴더" in alert.inner_text()
    assert page.get_by_role("tab").count() == 0


@pytest.mark.fr("FR-26")
def test_studio_npm_scripts_pass() -> None:
    for script in ("lint", "typecheck", "test", "build"):
        res = subprocess.run(
            ["npm", "--prefix", "studio", "run", script]
            if script != "test"
            else ["npm", "--prefix", "studio", "test"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert res.returncode == 0, f"{script} 실패:\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}"
