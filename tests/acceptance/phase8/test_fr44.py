"""FR-44 끌어다 놓기로 문서에 넣기 인수 테스트 (초안).

필요한 샘플: tests/fixtures/ 의 sales_q3.xlsx, company_intro.pptx (있음).
가정(구현 방법이 정해지면 맞춰 고친다):
- xlsx 탭이 활성일 때 화면에 `<label>범위</label>` 입력이 있고 `A1:C16`을 넣으면 끌 수 있는 요소
  (`button` 이름 `범위 끌기`)가 생긴다. 이것을 pptx 탭(`role=tab`)에 놓으면 docmill 도구가
  그 pptx에 표 또는 그래프를 넣고 작업 폴더의 파일을 갱신한다.
- provenance는 FR-21처럼 `<pptx 이름>.provenance.json`의 `numbers`에 쌓인다. 새로 들어온 숫자는
  `value`(넣은 숫자, 범위 안 숫자 셀의 합)·`source.file`(원본 경로, 끝이 sales_q3.xlsx)·
  `source.range`(A1:C16 안의 범위, 시작 ≤ 끝)·`location`을 가진다.
  표 칸·그래프 값으로 새로 들어간 숫자마다 provenance 항목이 있다.
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
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
RANGE = "A1:C16"
_RANGE_RE = re.compile(r"([A-C])(\d+)(?::([A-C])(\d+))?")
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"

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


def _bounds(rng: str) -> tuple[str, int, str, int] | None:
    """A1:C16 안의 올바른 범위(시작 ≤ 끝)면 (시작 열, 시작 행, 끝 열, 끝 행)을 돌려준다."""
    m = _RANGE_RE.fullmatch(rng)
    if not m:
        return None
    c1, r1 = m.group(1), int(m.group(2))
    c2, r2 = m.group(3) or c1, int(m.group(4) or r1)
    if not (1 <= r1 <= r2 <= 16 and c1 <= c2):
        return None
    return c1, r1, c2, r2


def _source_numbers() -> dict[str, float]:
    """sales_q3.xlsx의 숫자 셀(주소 → 값)."""
    with zipfile.ZipFile(FIXTURES / "sales_q3.xlsx") as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    cells: dict[str, float] = {}
    for addr, attrs, value in re.findall(r'<c r="([A-Z]+\d+)"([^>]*)><v>([^<]*)</v>', sheet):
        if 't="s"' not in attrs and 't="str"' not in attrs:
            cells[addr] = float(value)
    return cells


def _range_sum(rng: str) -> float | None:
    """범위 안 숫자 셀의 합. 숫자 셀이 없으면 None."""
    b = _bounds(rng)
    if b is None:
        return None
    c1, r1, c2, r2 = b
    picked = [
        v
        for addr, v in _source_numbers().items()
        if c1 <= addr[0] <= c2 and r1 <= int(addr[1:]) <= r2
    ]
    return sum(picked) if picked else None


def _shown_numbers(pptx: Path) -> Counter[float]:
    """표 칸 전체가 숫자인 값과 그래프 값(c:val)의 개수."""
    found: Counter[float] = Counter()
    with zipfile.ZipFile(pptx) as z:
        for name in z.namelist():
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name):
                for tc in ET.fromstring(z.read(name)).iter(f"{_A}tc"):
                    text = "".join(t.text or "" for t in tc.iter(f"{_A}t")).strip()
                    if _NUM.fullmatch(text):
                        found[float(text.replace(",", ""))] += 1
            if re.fullmatch(r"ppt/charts/chart\d+\.xml", name):
                for val in ET.fromstring(z.read(name)).iter(f"{_C}val"):
                    for v in val.iter(f"{_C}v"):
                        if v.text and _NUM.fullmatch(v.text.strip()):
                            found[float(v.text.replace(",", ""))] += 1
    return found


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
    shown_before = _shown_numbers(pptx)
    page.goto(start_studio(workspace=ws))
    _drop_range(page)
    assert _wait(lambda: _structure(pptx) > before), "표 또는 그래프가 생겨야 한다"
    assert _wait(lambda: _provenance_ready(pptx)), "provenance가 기록돼야 한다"
    inserted = _shown_numbers(pptx) - shown_before
    assert inserted, "넣은 표·그래프에 원본 숫자가 들어가야 한다"
    entries = _prov(pptx)
    for n in entries:
        assert str(n["source"]["file"]).endswith("sales_q3.xlsx"), n
        expected = _range_sum(str(n["source"]["range"]))
        assert expected is not None, ("A1:C16 안의 숫자 범위여야 한다", n)
        assert abs(float(n["value"]) - expected) < 1e-6, ("기록 값이 원본 범위 값과 같아야 한다", n)
        assert n["location"], n
    recorded = Counter(float(n["value"]) for n in entries)
    assert not inserted - recorded, ("넣은 숫자마다 provenance가 있어야 한다", inserted, recorded)


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
    with zipfile.ZipFile(changed) as z:
        assert b'<c r="C2" t="n"><v>1500</v>' in z.read("xl/worksheets/sheet1.xml")
    shutil.copy(changed, ws / "sales_q3.xlsx")  # 원본이 저장된 것과 같은 효과
    page.get_by_text("원본이 바뀌었어요").wait_for(timeout=60_000)
    page.get_by_role("button", name="새 숫자로 고치기").wait_for()
