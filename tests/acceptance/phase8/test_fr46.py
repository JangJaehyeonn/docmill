"""FR-46 끝까지 보내기 (PDF·폴더·메일 초안) 인수 테스트 (초안).

필요한 샘플: tests/fixtures/company_intro.pptx (3장, 있음). 개인정보가 든 docx는 테스트가 zipfile로 만든다.
가정(구현 방법이 정해지면 맞춰 고친다):
- 문서 탭이 활성일 때 `내보내기` 버튼 → 대화상자(`role=dialog`)에서 `PDF`·`폴더에 저장`·`메일 초안` 중
  하나를 고르는 라디오(`<label>`)와 `실행` 버튼이 있다.
- PDF는 작업 폴더에 `*.pdf`로, 메일 초안은 작업 폴더에 `*.eml`로 생긴다.
- 내보내기 전에 preflight(FR-34)를 자동으로 돌린다. 실패 항목이 있으면 `role=dialog`에
  '점검' 문구와 `그래도 내보내기`·`취소` 버튼이 보이고, 확인 전에는 파일이 생기지 않는다.
- 메일은 직접 보내지 않는다: 화면에 '보냈'이 나오지 않고 SMTP 설정이 비어 있어도 .eml이 생긴다.
- PDF(pptx)는 ONLYOFFICE가 필요하므로 DOCMILL_ONLYOFFICE_URL이 없으면 실패한다.
- 이 파일은 tests/acceptance/ 로 옮겨졌을 때 conftest의 start_studio·page 픽스처를 쓴다.
"""

# ruff: noqa: E501

from __future__ import annotations

import email
import email.policy
import os
import re
import shutil
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
RRN = "900101-1234567"

_FR = "FR-46"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


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


def _open(page: Any, name: str) -> None:
    page.get_by_role("listitem").filter(has_text=name).first.click()
    page.get_by_role("tab", name=name).wait_for()


def _export(page: Any, kind: str) -> None:
    page.get_by_role("button", name="내보내기").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label(kind).check()
    dialog.get_by_role("button", name="실행").click()


def _wait_files(ws: Path, pattern: str, seconds: int = 90) -> list[Path]:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        found = sorted(ws.glob(pattern))
        if found:
            return found
        time.sleep(1)
    return []


def _pdf_pages(pdf: Path) -> int:
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF"), "PDF 파일이어야 한다"
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", data))


@pytest.mark.fr("FR-46")
def test_pptx_pdf_export_page_count_equals_slide_count(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    assert os.environ.get("DOCMILL_ONLYOFFICE_URL"), "DOCMILL_ONLYOFFICE_URL이 필요하다"
    ws = tmp_path / "ws"
    ws.mkdir()
    shutil.copy(FIXTURES / "company_intro.pptx", ws / "company_intro.pptx")
    with zipfile.ZipFile(ws / "company_intro.pptx") as z:
        slides = len([n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)])
    assert slides == 3
    page.goto(start_studio(workspace=ws))
    _open(page, "company_intro.pptx")
    _export(page, "PDF")
    pdfs = _wait_files(ws, "*.pdf")
    assert pdfs, "작업 폴더에 PDF가 생겨야 한다"
    assert _pdf_pages(pdfs[0]) == slides


@pytest.mark.fr("FR-46")
def test_mail_draft_makes_eml_with_attachment_and_sends_nothing(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _make_docx(ws / "report.docx", ["주간 보고", "이번 주 일정을 점검했다."])
    page.goto(start_studio(workspace=ws))
    _open(page, "report.docx")
    _export(page, "메일 초안")
    emls = _wait_files(ws, "*.eml")
    assert emls, "작업 폴더에 .eml 파일이 생겨야 한다"
    msg = email.message_from_bytes(emls[0].read_bytes(), policy=email.policy.default)
    assert isinstance(msg, email.message.EmailMessage)
    names = [part.get_filename() for part in msg.iter_attachments()]
    assert "report.docx" in names, names
    assert "보냈" not in page.locator("body").inner_text()


@pytest.mark.fr("FR-46")
def test_preflight_failure_shows_confirm_dialog_before_export(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _make_docx(ws / "secret.docx", ["주간 보고", f"담당자 주민등록번호 {RRN}"])
    page.goto(start_studio(workspace=ws))
    _open(page, "secret.docx")
    _export(page, "메일 초안")
    dialog = page.get_by_role("dialog")
    dialog.get_by_text("점검").first.wait_for(timeout=60_000)
    dialog.get_by_role("button", name="그래도 내보내기").wait_for()
    dialog.get_by_role("button", name="취소").wait_for()
    assert not list(ws.glob("*.eml")), "확인 전에는 파일이 생기면 안 된다"
    dialog.get_by_role("button", name="그래도 내보내기").click()
    assert _wait_files(ws, "*.eml"), "확인하면 내보내기가 진행돼야 한다"
