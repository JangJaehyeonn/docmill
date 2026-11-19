"""FR-48 비슷한 문서 찾기 인수 테스트 (초안).

필요한 샘플: 없음. 주간 보고 docx 3개와 방해용 회의록 1개는 테스트가 zipfile로 만들고
`os.utime`으로 수정 시각을 고정한다 (파일 이름 순서와 최근 순서가 다르게 둔다).
가정(구현 방법이 정해지면 맞춰 고친다):
- 시작 화면에 `<label>작업</label>` 입력과 `작업 시작` 버튼이 있다. '주간 보고'를 넣고 누르면
  `region` "비슷한 문서" 안에 `listitem`으로 제안이 나오고, 맨 위부터 가까운 순서다
  (제목이 같으면 최근 수정 순). 제안 문구에 '이걸로 시작할까요?'가 있다.
- 비슷하지 않은 문서(회의록)는 제안 목록에 나오지 않는다.
- 이 파일은 tests/acceptance/ 로 옮겨졌을 때 conftest의 start_studio·page 픽스처를 쓴다.
"""

# ruff: noqa: E501

from __future__ import annotations

import os
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pytest

# (파일 이름, 제목, 수정 시각(초)) — 가장 최근은 weekly_b.docx 이다.
WEEKLY = [
    ("weekly_a.docx", "주간 보고", 1_700_000_000),
    ("weekly_b.docx", "주간 보고", 1_700_900_000),
    ("weekly_c.docx", "주간 보고", 1_700_400_000),
]

_FR = "FR-48"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


def _make_docx(path: Path, paragraphs: list[str]) -> None:
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


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    for name, title, mtime in WEEKLY:
        _make_docx(ws / name, [title, "이번 주 진행 상황", "다음 주 계획"])
        os.utime(ws / name, (mtime, mtime))
    _make_docx(ws / "minutes.docx", ["회의록", "참석자", "안건"])
    os.utime(ws / "minutes.docx", (1_700_990_000, 1_700_990_000))  # 가장 최근이지만 비슷하지 않다
    return ws


def _suggestions(page: Any, url: str) -> list[str]:
    page.goto(url)
    page.get_by_label("작업").fill("주간 보고")
    page.get_by_role("button", name="작업 시작").click()
    region = page.get_by_role("region", name="비슷한 문서")
    region.get_by_role("listitem").first.wait_for(timeout=30_000)
    assert region.get_by_text("이걸로 시작할까요?").first.is_visible()
    items = region.get_by_role("listitem")
    return [items.nth(i).inner_text() for i in range(items.count())]


@pytest.mark.fr("FR-48")
def test_most_recent_weekly_report_is_first_suggestion(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    url = start_studio(workspace=_workspace(tmp_path))
    items = _suggestions(page, url)
    assert "weekly_b.docx" in items[0], items
    assert not [t for t in items if "minutes.docx" in t], "비슷하지 않은 문서는 제안하지 않는다"


@pytest.mark.fr("FR-48")
def test_same_folder_gives_same_order(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    ws = _workspace(tmp_path)
    first = _suggestions(page, start_studio(workspace=ws))
    second = _suggestions(page, start_studio(workspace=ws))
    assert first == second
    order = [n for t in first for n in ("weekly_a", "weekly_b", "weekly_c") if n in t]
    assert order == ["weekly_b", "weekly_c", "weekly_a"], first  # 최근 수정 순
