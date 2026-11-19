"""FR-44 끌어다 놓기로 문서에 넣기 인수 테스트 (초안).

필요한 샘플: tests/fixtures/ 의 sales_q3.xlsx, company_intro.pptx (있음).
가정(구현 방법이 정해지면 맞춰 고친다):
- xlsx 탭이 활성일 때 화면에 `<label>범위</label>` 입력이 있고 `A1:C16`을 넣으면 끌 수 있는 요소
  (`button` 이름 `범위 끌기`)가 생긴다. 이것을 pptx 탭(`role=tab`)에 놓으면 docmill 도구가
  그 pptx에 표 또는 그래프를 넣고 작업 폴더의 파일을 갱신한다.
- provenance는 FR-21처럼 `<pptx 이름>.provenance.json`의 `numbers`에 쌓인다. 새로 들어온 숫자는
  `source.file`(원본 경로, 끝이 sales_q3.xlsx)·`source.range`(A1:C16 안의 범위)·`location`을 가진다.
- 원본이 바뀌면 FR-37처럼 '원본이 바뀌었어요' 문구와 `새 숫자로 고치기` 버튼이 나온다.
- ONLYOFFICE가 필요하므로 DOCMILL_ONLYOFFICE_URL이 없으면 실패한다.
- 이 파일은 tests/acceptance/ 로 옮겨졌을 때 conftest의 start_studio·page 픽스처를 쓴다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
RANGE = "A1:C16"
_RANGE_RE = re.compile(r"([A-C])(\d+)(?::([A-C])(\d+))?")

_FR = "FR-44"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    shutil.copy(FIXTURES / "sales_q3.xlsx", ws / "sales_q3.xlsx")
    shutil.copy(FIXTURES / "company_intro.pptx", ws / "company_intro.pptx")
    return ws


def _open(page: Any, name: str) -> None:
    page.get_by_role("listitem").filter(has_text=name).first.click()
    page.get_by_role("tab", name=name).wait_for()


def _drop_range(page: Any) -> None:
    _open(page, "sales_q3.xlsx")
    _open(page, "company_intro.pptx")
    page.get_by_role("tab", name="sales_q3.xlsx").click()
    page.get_by_label("범위").fill(RANGE)
    page.get_by_role("button", name="범위 끌기").drag_to(
        page.get_by_role("tab", name="company_intro.pptx")
    )


def _structure(pptx: Path) -> int:
    """슬라이드의 표 개수 + 그래프 파트 개수."""
    count = 0
    with zipfile.ZipFile(pptx) as z:
        for name in z.namelist():
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name):
                count += z.read(name).count(b"<a:tbl>")
            if re.fullmatch(r"ppt/charts/chart\d+\.xml", name):
                count += 1
    return count


def _prov(pptx: Path) -> list[dict[str, Any]]:
    path = pptx.with_name(pptx.name + ".provenance.json")
    return list(json.loads(path.read_text(encoding="utf-8"))["numbers"])


def _wait(cond: Callable[[], bool], seconds: int = 90) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(1)
    return False


def _inside(rng: str) -> bool:
    m = _RANGE_RE.fullmatch(rng)
    if not m:
        return False
    return int(m.group(2)) >= 1 and int(m.group(4) or m.group(2)) <= 16


def _provenance_ready(pptx: Path) -> bool:
    try:
        return bool(_prov(pptx))
    except (OSError, ValueError, KeyError):
        return False


@pytest.mark.fr("FR-44")
def test_drop_range_on_pptx_adds_table_or_chart_with_provenance(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    assert os.environ.get("DOCMILL_ONLYOFFICE_URL"), "DOCMILL_ONLYOFFICE_URL이 필요하다"
    ws = _ws(tmp_path)
    pptx = ws / "company_intro.pptx"
    before = _structure(pptx)
    page.goto(start_studio(workspace=ws))
    _drop_range(page)
    assert _wait(lambda: _structure(pptx) > before), "표 또는 그래프가 생겨야 한다"
    assert _wait(lambda: _provenance_ready(pptx)), "provenance가 기록돼야 한다"
    for n in _prov(pptx):
        assert str(n["source"]["file"]).endswith("sales_q3.xlsx"), n
        assert _inside(str(n["source"]["range"])), n
        assert n["location"], n


@pytest.mark.fr("FR-44")
def test_source_change_after_drop_offers_refresh_for_that_document(
    start_studio: Callable[..., str], page: Any, tmp_path: Path
) -> None:
    assert os.environ.get("DOCMILL_ONLYOFFICE_URL"), "DOCMILL_ONLYOFFICE_URL이 필요하다"
    ws = _ws(tmp_path)
    pptx = ws / "company_intro.pptx"
    before = _structure(pptx)
    page.goto(start_studio(workspace=ws))
    _drop_range(page)
    assert _wait(lambda: _structure(pptx) > before and _provenance_ready(pptx))
    changed = tmp_path / "changed.xlsx"
    with (
        zipfile.ZipFile(FIXTURES / "sales_q3.xlsx") as zin,
        zipfile.ZipFile(changed, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(
                    b'<c r="C2" t="n"><v>1200</v>', b'<c r="C2" t="n"><v>1500</v>', 1
                )
            zout.writestr(item, data)
    shutil.copy(changed, ws / "sales_q3.xlsx")  # 원본이 저장된 것과 같은 효과
    page.get_by_text("원본이 바뀌었어요").wait_for(timeout=60_000)
    page.get_by_role("button", name="새 숫자로 고치기").wait_for()
