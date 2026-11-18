"""FR-43 결재 전 리허설 (register_reviewer / rehearse_review) 인수 테스트 (초안).

필요한 샘플: tests/fixtures/sales_q3.xlsx (있음). 검토할 pptx는 excel_to_report_ppt(slides=5)로 만들고,
테스트 안에서 zip을 고쳐 출처 없는 숫자를 넣는다.
가정(구현 방법이 정해지면 맞춰 고친다):
- `register_reviewer` 인자: `name`, `comments`(예전 결재 코멘트 문자열 목록) 또는 `comments_file`(docx 메모·텍스트).
  결과 JSON: `reviewer`, `rules` 목록. 각 규칙은 `kind`(`길이`·`근거`·`결론 위치`)와
  길이 규칙이면 `limit`(장 수)을 가진다. 기준은 DOCMILL_HOME의 저장소에 남아 다음 세션에서도 쓰인다.
- `rehearse_review` 인자: `reviewer`, `document`. 결과 JSON: `items` 목록(각 항목 `reason`, `location`, `text`,
  길이 초과면 `current`=현재 분량(장 수)). 등록하지 않은 이름이면 `error` 없이 `message`에 '기준이 없어요'를 돌려준다.
- 근거 규칙은 review_map(FR-33)의 `출처 없음` 결과를 사용하므로 항목 reason도 `출처 없음`이다.
"""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import os
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

_FR = "FR-43"
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


def _report(home: Path, slides: int = 5) -> Path:
    out = _sync(
        home,
        "excel_to_report_ppt",
        {
            "input": str(SAMPLE),
            "group_by": "부서",
            "value": "매출",
            "slides": slides,
            "chart": "bar",
            "verify": False,
        },
    )
    return Path(out["path"])


def _prov(pptx: Path) -> Path:
    return pptx.with_name(pptx.name + ".provenance.json")


def _inject_unsourced(src: Path, dst: Path) -> None:
    shape = (
        '<p:sp><p:nvSpPr><p:cNvPr id="900" name="Injected"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        "<p:spPr/><p:txBody><a:bodyPr/><a:p><a:r><a:t>"
        f"기타 비용 {UNSOURCED}"
        "</a:t></a:r></a:p></p:txBody></p:sp>"
    )
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "ppt/slides/slide1.xml":
                data = data.replace(b"</p:spTree>", shape.encode("utf-8") + b"</p:spTree>", 1)
            zout.writestr(item, data)
    shutil.copy(_prov(src), _prov(dst))


@pytest.mark.fr("FR-43")
def test_length_comments_store_a_length_rule(tmp_path: Path) -> None:
    out = _sync(
        tmp_path,
        "register_reviewer",
        {"name": "김부장", "comments": ["너무 길다", "한 장으로 줄여"]},
    )
    lengths = [r for r in out["rules"] if r["kind"] == "길이"]
    assert len(lengths) == 1, out
    assert int(lengths[0]["limit"]) == 1


@pytest.mark.fr("FR-43")
def test_longer_document_gets_length_exceeded_with_current_size(tmp_path: Path) -> None:
    _sync(tmp_path, "register_reviewer", {"name": "김부장", "comments": ["너무 길다", "한 장으로 줄여"]})
    report = _report(tmp_path, slides=5)
    # 등록은 별도 MCP 세션이었으므로 저장소에 남은 기준을 쓰는지도 함께 확인된다.
    out = _sync(tmp_path, "rehearse_review", {"reviewer": "김부장", "document": str(report)})
    hits = [i for i in out["items"] if i["reason"] == "길이 초과"]
    assert len(hits) == 1, out
    assert str(hits[0]["current"]).startswith("5") or "5" in json.dumps(hits[0], ensure_ascii=False)


@pytest.mark.fr("FR-43")
def test_evidence_comment_flags_unsourced_numbers(tmp_path: Path) -> None:
    _sync(tmp_path, "register_reviewer", {"name": "이과장", "comments": ["이 숫자 근거가 뭐야"]})
    report = _report(tmp_path)
    clean = _sync(tmp_path, "rehearse_review", {"reviewer": "이과장", "document": str(report)})
    assert [i for i in clean["items"] if i["reason"] == "출처 없음"] == []
    doc = tmp_path / "injected" / "doc.pptx"
    doc.parent.mkdir()
    _inject_unsourced(report, doc)
    out = _sync(tmp_path, "rehearse_review", {"reviewer": "이과장", "document": str(doc)})
    flagged = [i for i in out["items"] if i["reason"] == "출처 없음"]
    assert len(flagged) == 1 and UNSOURCED in flagged[0]["text"]
    # 길이 코멘트는 등록하지 않았으므로 길이 초과는 나오면 안 된다.
    assert [i for i in out["items"] if i["reason"] == "길이 초과"] == []


@pytest.mark.fr("FR-43")
def test_unregistered_reviewer_gets_no_rules_message(tmp_path: Path) -> None:
    report = _report(tmp_path)
    out = _sync(tmp_path, "rehearse_review", {"reviewer": "박사장", "document": str(report)})
    assert "error" not in out
    assert "기준이 없어요" in json.dumps(out, ensure_ascii=False)
