"""FR-33 검토할 곳만 짚어 주기 (review_map) 인수 테스트 (초안).

필요한 샘플: tests/fixtures/sales_q3.xlsx (있음). 검토 대상 pptx는 FR-05 `excel_to_report_ppt`로 만들고,
테스트 안에서 zip을 고쳐 "출처 없는 숫자"·"근거 없는 예측 문장"·"바뀐 숫자"를 넣는다.
가정(구현 방법이 정해지면 맞춰 고친다):
- MCP 도구 `review_map` 인자: `document`, 선택 `baseline`(기준 버전 문서 경로).
  출처 파일은 FR-21처럼 문서 옆 `<문서 이름>.provenance.json` 이다 (테스트가 복사해 둔다).
- 결과 JSON: `items` 목록. 각 항목은 `location`, `reason`('출처 없음' · '근거 없는 예측' · '이번에 바뀜'),
  `text`(해당 숫자·문장)를 가진다.
"""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE = FIXTURES / "sales_q3.xlsx"
UNSOURCED = "9,999"
PREDICTION = "다음 달도 증가가 예상된다"

_FR = "FR-33"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


async def _call(home: Path, name: str, args: dict[str, Any]) -> dict[str, Any]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "docmill.cli", "mcp"],
        env={**os.environ, "DOCMILL_HOME": str(home)},
        cwd=str(home),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(name, args)
    text = "".join(getattr(c, "text", "") for c in result.content)
    return dict(json.loads(text))


def _sync(home: Path, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(asyncio.wait_for(_call(home, name, args), 90))


def _report(home: Path) -> Path:
    out = _sync(
        home,
        "excel_to_report_ppt",
        {
            "input": str(SAMPLE),
            "group_by": "부서",
            "value": "매출",
            "slides": 5,
            "chart": "bar",
            "verify": False,
        },
    )
    return Path(out["path"])


def _prov(pptx: Path) -> Path:
    return pptx.with_name(pptx.name + ".provenance.json")


def _inject_text(src: Path, dst: Path, text: str) -> None:
    """슬라이드 1 맨 끝에 텍스트 도형을 하나 덧붙인 복사본을 만들고 출처 파일도 복사한다."""
    shape = (
        '<p:sp><p:nvSpPr><p:cNvPr id="900" name="Injected"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        "<p:spPr/><p:txBody><a:bodyPr/><a:p><a:r><a:t>"
        + text
        + "</a:t></a:r></a:p></p:txBody></p:sp>"
    )
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "ppt/slides/slide1.xml":
                data = data.replace(b"</p:spTree>", shape.encode("utf-8") + b"</p:spTree>", 1)
            zout.writestr(item, data)
    shutil.copy(_prov(src), _prov(dst))


def _bump_chart_value(src: Path, dst: Path) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        done = False
        for item in zin.infolist():
            data = zin.read(item.filename)
            if not done and re.fullmatch(r"ppt/charts/chart\d+\.xml", item.filename):
                text = data.decode("utf-8")
                m = re.search(r"<c:val>.*?<c:v>([^<]+)</c:v>", text, re.S)
                assert m
                text = text[: m.start(1)] + str(float(m.group(1)) + 1) + text[m.end(1) :]
                data = text.encode("utf-8")
                done = True
            zout.writestr(item, data)
    shutil.copy(_prov(src), _prov(dst))


def _review(home: Path, doc: Path, baseline: Path | None = None) -> list[dict[str, Any]]:
    args: dict[str, Any] = {"document": str(doc)}
    if baseline:
        args["baseline"] = str(baseline)
    return list(_sync(home, "review_map", args)["items"])


@pytest.mark.fr("FR-33")
def test_unsourced_number_is_flagged_and_sourced_numbers_are_not(tmp_path: Path) -> None:
    report = _report(tmp_path)
    clean = _review(tmp_path, report)
    assert [i for i in clean if i["reason"] == "출처 없음"] == [], "출처 있는 숫자는 나오면 안 된다"
    doc = tmp_path / "injected" / "doc.pptx"
    doc.parent.mkdir()
    _inject_text(report, doc, f"기타 비용 {UNSOURCED}")
    flagged = [i for i in _review(tmp_path, doc) if i["reason"] == "출처 없음"]
    assert len(flagged) == 1
    assert UNSOURCED in flagged[0]["text"] and flagged[0]["location"]


@pytest.mark.fr("FR-33")
def test_unfounded_prediction_sentence_is_flagged(tmp_path: Path) -> None:
    report = _report(tmp_path)
    doc = tmp_path / "pred" / "doc.pptx"
    doc.parent.mkdir()
    _inject_text(report, doc, f"{PREDICTION}.")
    items = [i for i in _review(tmp_path, doc) if i["reason"] == "근거 없는 예측"]
    assert len(items) == 1 and PREDICTION in items[0]["text"]


@pytest.mark.fr("FR-33")
def test_baseline_marks_only_changed_parts(tmp_path: Path) -> None:
    base = _report(tmp_path)
    same = _review(tmp_path, base, baseline=base)
    assert [i for i in same if i["reason"] == "이번에 바뀜"] == []

    changed = tmp_path / "changed" / "doc.pptx"
    changed.parent.mkdir()
    _bump_chart_value(base, changed)
    items = [i for i in _review(tmp_path, changed, baseline=base) if i["reason"] == "이번에 바뀜"]
    assert len(items) == 1, items

    with_sentence = tmp_path / "sentence" / "doc.pptx"
    with_sentence.parent.mkdir()
    _inject_text(base, with_sentence, "새로 쓴 문장입니다.")
    sentence_items = [
        i for i in _review(tmp_path, with_sentence, baseline=base) if i["reason"] == "이번에 바뀜"
    ]
    assert len(sentence_items) == 1 and "새로 쓴 문장입니다" in sentence_items[0]["text"]


@pytest.mark.fr("FR-33")
def test_same_input_gives_same_list_in_same_order(tmp_path: Path) -> None:
    report = _report(tmp_path)
    doc = tmp_path / "x" / "doc.pptx"
    doc.parent.mkdir()
    _inject_text(report, doc, f"{PREDICTION}. 기타 비용 {UNSOURCED}")
    first = _review(tmp_path, doc, baseline=report)
    second = _review(tmp_path, doc, baseline=report)
    assert first and first == second
