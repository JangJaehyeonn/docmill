"""FR-42 이미지 생성·삽입 (insert_image) 인수 테스트 (초안).

필요한 샘플: tests/fixtures/company_intro.pptx(3장), meeting_1111.docx, meeting_1111.hwp (있음).
고정 PNG는 테스트가 직접 만든다 (표준 라이브러리 zlib·struct).
가정(구현 방법이 정해지면 맞춰 고친다):
- MCP 도구 `insert_image` 인자: `document`, `prompt`(설명 문장), 위치 — pptx는 `slide`(1부터),
  docx·hwp는 `after_paragraph`(1부터 문단 번호), 선택 `provider`(`openai`·`gemini`·`fake`).
- 가짜 제공자: `provider="fake"`이면 환경 변수 `DOCMILL_FAKE_IMAGE_PNG`가 가리키는 PNG 파일을 그대로 돌려준다.
- 키 환경 변수: openai → `OPENAI_API_KEY`, gemini → `GEMINI_API_KEY`.
- 결과 JSON: 성공하면 `path`(편집된 문서), 꺼져 있으면 `{"disabled": true, "message": "이미지 생성이 꺼져 있어요 ... <환경 변수 이름>"}`.
  `path`는 입력 문서와 다른 새 파일이고 원본은 그대로다 (guarded_edit 경유: 작업별 임시 폴더).
"""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import os
import re
import struct
import sys
import zipfile
import zlib
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

_FR = "FR-42"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


def _png(path: Path) -> Path:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


async def _call(home: Path, name: str, args: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    full = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "GEMINI_API_KEY")}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "docmill.cli", "mcp"],
        env={**full, "DOCMILL_HOME": str(home), **env},
        cwd=str(home),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(name, args)
    text = "".join(getattr(c, "text", "") for c in result.content)
    return dict(json.loads(text))


def _insert(home: Path, args: dict[str, Any], env: dict[str, str] | None = None) -> dict[str, Any]:
    return asyncio.run(asyncio.wait_for(_call(home, "insert_image", args, env or {}), 120))


def _fake_env(tmp_path: Path) -> dict[str, str]:
    return {"DOCMILL_FAKE_IMAGE_PNG": str(_png(tmp_path / "fixed.png"))}


def _slide_pics(pptx: Path) -> dict[int, int]:
    counts: dict[int, int] = {}
    with zipfile.ZipFile(pptx) as z:
        for name in z.namelist():
            m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name)
            if m:
                counts[int(m.group(1))] = len(
                    re.findall(r"<p:pic[ >]", z.read(name).decode("utf-8"))
                )
    return counts


def _slide_xml(pptx: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(pptx) as z:
        return {n: z.read(n) for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)}


@pytest.mark.fr("FR-42")
def test_pptx_slide_three_gets_one_image_and_other_slides_are_untouched(tmp_path: Path) -> None:
    src = FIXTURES / "company_intro.pptx"
    before_pics = _slide_pics(src)
    out = _insert(
        tmp_path,
        {"document": str(src), "slide": 3, "prompt": "푸른 하늘 아래 사무실", "provider": "fake"},
        _fake_env(tmp_path),
    )
    result = Path(out["path"])
    assert result != src and result.exists()
    after = _slide_pics(result)
    assert after[3] == before_pics[3] + 1
    assert {k: v for k, v in after.items() if k != 3} == {
        k: v for k, v in before_pics.items() if k != 3
    }
    old_xml, new_xml = _slide_xml(src), _slide_xml(result)
    for name, data in old_xml.items():
        if not name.endswith("slide3.xml"):
            assert new_xml[name] == data, f"{name}이 바뀌었다"
    with zipfile.ZipFile(result) as z:
        assert any(n.startswith("ppt/media/") for n in z.namelist())


@pytest.mark.fr("FR-42")
def test_docx_image_is_added_after_paragraph(tmp_path: Path) -> None:
    src = FIXTURES / "meeting_1111.docx"
    with zipfile.ZipFile(src) as z:
        before = z.read("word/document.xml").decode("utf-8").count("<w:drawing")
    out = _insert(
        tmp_path,
        {"document": str(src), "after_paragraph": 3, "prompt": "회의실 그림", "provider": "fake"},
        _fake_env(tmp_path),
    )
    with zipfile.ZipFile(out["path"]) as z:
        after = z.read("word/document.xml").decode("utf-8").count("<w:drawing")
        assert any(n.startswith("word/media/") for n in z.namelist())
    assert after == before + 1


@pytest.mark.fr("FR-42")
def test_hwp_image_is_added_after_paragraph(tmp_path: Path) -> None:
    src = FIXTURES / "meeting_1111.hwp"
    out = _insert(
        tmp_path,
        {"document": str(src), "after_paragraph": 3, "prompt": "회의실 그림", "provider": "fake"},
        _fake_env(tmp_path),
    )
    result = Path(out["path"])
    assert result.exists() and result.suffix == ".hwp"
    assert result.stat().st_size > src.stat().st_size, "그림이 들어갔으면 파일이 커진다"


@pytest.mark.fr("FR-42")
@pytest.mark.parametrize(
    ("provider", "key"), [("openai", "OPENAI_API_KEY"), ("gemini", "GEMINI_API_KEY")]
)
def test_missing_key_turns_feature_off_with_reason(tmp_path: Path, provider: str, key: str) -> None:
    out = _insert(
        tmp_path,
        {
            "document": str(FIXTURES / "company_intro.pptx"),
            "slide": 3,
            "prompt": "푸른 하늘",
            "provider": provider,
        },
    )
    text = json.dumps(out, ensure_ascii=False)
    assert "이미지 생성이 꺼져 있어요" in text
    assert key in text
    assert "traceback" not in text.lower()
