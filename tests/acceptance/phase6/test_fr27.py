"""FR-27 엑셀·PPT·워드 편집 화면 (ONLYOFFICE) 인수 테스트 (초안).

필요한 샘플: tests/fixtures/ 의 sales_q3.xlsx, company_intro.pptx, meeting_1111.docx (있음).
환경: DOCMILL_ONLYOFFICE_URL (없으면 실패. CI는 항상 띄운다).
가정(구현 방법이 정해지면 맞춰 고친다):
- 목록에서 파일을 열면 탭이 생기고, 탭 패널 안에 ONLYOFFICE 편집기 iframe이 뜬다.
- 편집기가 문서 준비 완료가 되면 패널의 `data-editor-state="ready"` 속성이 붙는다 (`role=tabpanel`).
- 편집기 API 접근을 위해 페이지에 `window.docEditor`(ONLYOFFICE DocsAPI.DocEditor 인스턴스)가 노출된다.
  셀 값 변경은 편집기 커맨드 서비스(`createConnector` → `executeMethod`/`callCommand`)로 한다.
- 저장은 편집기 API로 강제 저장(`window.docEditor.downloadAs` 후 서버가 콜백 처리) 또는 앱의 `저장` 버튼이다.
  여기서는 `저장` 버튼(`role=button`, name=저장)을 누른다고 가정한다.
- 연결 안내 문구: '문서 서버에 연결되지 않았어요'.
"""

# ruff: noqa: E501, F811, F401

from __future__ import annotations

import os
import re
import shutil
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.acceptance.conftest import page, start_studio

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
FILES = ["sales_q3.xlsx", "company_intro.pptx", "meeting_1111.docx"]

_FR = "FR-27"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


def _need_onlyoffice() -> str:
    url = os.environ.get("DOCMILL_ONLYOFFICE_URL")
    if not url:
        pytest.fail("DOCMILL_ONLYOFFICE_URL이 필요합니다 (CI는 항상 문서 서버를 띄웁니다).")
    return url


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    for name in FILES:
        shutil.copy(FIXTURES / name, ws / name)
    return ws


def _open(page: Any, name: str) -> None:
    page.get_by_role("listitem").filter(has_text=name).first.click()
    page.get_by_role("tab", name=name).wait_for()


def _sheet_strings(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        out = [z.read(n).decode("utf-8") for n in z.namelist() if n.startswith("xl/")]
    return out


@pytest.mark.fr("FR-27")
def test_editors_become_ready_for_each_document_type(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    onlyoffice = _need_onlyoffice()
    url = start_studio(workspace=_workspace(tmp_path), env={"DOCMILL_ONLYOFFICE_URL": onlyoffice})
    page.goto(url)
    for name in FILES:
        _open(page, name)
        page.locator('[role="tabpanel"][data-editor-state="ready"]').first.wait_for(timeout=120_000)


@pytest.mark.fr("FR-27")
def test_cell_edit_and_save_updates_original_xlsx(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    onlyoffice = _need_onlyoffice()
    ws = _workspace(tmp_path)
    url = start_studio(workspace=ws, env={"DOCMILL_ONLYOFFICE_URL": onlyoffice})
    page.goto(url)
    _open(page, "sales_q3.xlsx")
    page.locator('[role="tabpanel"][data-editor-state="ready"]').wait_for(timeout=120_000)
    marker = "docmill-fr27-marker"
    page.evaluate(
        """(marker) => new Promise((resolve) => {
            const connector = window.docEditor.createConnector();
            connector.callCommand(
              "Api.GetActiveSheet().GetRange('D1').SetValue('" + marker + "');",
              () => resolve(true));
        })""",
        marker,
    )
    page.get_by_role("button", name="저장").click()
    saved = ws / "sales_q3.xlsx"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if any(marker in text for text in _sheet_strings(saved)):
            break
        time.sleep(1)
    assert any(marker in text for text in _sheet_strings(saved)), "60초 안에 원본에 반영돼야 한다"
    assert re.search(r"부서", "".join(_sheet_strings(saved))), "기존 내용은 유지돼야 한다"


@pytest.mark.fr("FR-27")
def test_without_document_server_shows_notice_and_app_keeps_working(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    env = {k: v for k, v in os.environ.items() if k != "DOCMILL_ONLYOFFICE_URL"}
    env["DOCMILL_ONLYOFFICE_URL"] = ""
    url = start_studio(workspace=_workspace(tmp_path), env=env)
    page.goto(url)
    _open(page, "sales_q3.xlsx")
    page.get_by_text("문서 서버에 연결되지 않았어요").wait_for()
    # 다른 탭(화면 동작)은 정상: 두 번째 파일도 열리고 탭이 전환된다.
    _open(page, "meeting_1111.docx")
    assert page.get_by_role("tab").count() == 2
    page.get_by_role("tab", name="sales_q3.xlsx").click()
    assert page.get_by_role("tab", name="sales_q3.xlsx").get_attribute("aria-selected") == "true"
