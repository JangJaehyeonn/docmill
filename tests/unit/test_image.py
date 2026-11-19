"""insert_image 단위 테스트 (FR-42). 실제 제공자는 부르지 않고 가짜 제공자를 쓴다."""

from __future__ import annotations

import json
import re
import struct
import zipfile
import zlib
from pathlib import Path

import pytest

from docmill.tools.image import ImageDisabled, generate_image, insert_image, png_size

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


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


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DOCMILL_FAKE_IMAGE_PNG", str(_png(tmp_path / "fixed.png")))
    return tmp_path


def test_png_size_and_rejects_non_png(tmp_path: Path) -> None:
    assert png_size(_png(tmp_path / "x.png").read_bytes()) == (4, 4)
    with pytest.raises(ValueError):
        png_size(b"not a png at all, just text bytes")


def test_pptx_slide_gets_picture_and_package_is_consistent(fake: Path) -> None:
    src = FIXTURES / "company_intro.pptx"
    out = json.loads(
        insert_image(str(src), "하늘", slide=3, provider="fake", out_dir=str(fake / "out"))
    )
    assert out["out_of_scope_changes"] == 0
    with zipfile.ZipFile(out["path"]) as z:
        slide = z.read("ppt/slides/slide3.xml").decode()
        rels = z.read("ppt/slides/_rels/slide3.xml.rels").decode()
        types = z.read("[Content_Types].xml").decode()
        assert slide.count("<p:pic ") == 1
        rid = re.search(r'r:embed="(rId\d+)"', slide)
        assert rid and f'Id="{rid.group(1)}"' in rels
        assert "../media/image1.png" in rels
        assert 'Extension="png"' in types
        assert z.read("ppt/media/image1.png")
        assert z.testzip() is None
    with zipfile.ZipFile(src) as z:
        assert z.read("ppt/slides/slide1.xml") == zipfile.ZipFile(out["path"]).read(
            "ppt/slides/slide1.xml"
        )


def test_same_input_gives_same_bytes(fake: Path) -> None:
    src = FIXTURES / "company_intro.pptx"
    a = json.loads(insert_image(str(src), "x", slide=3, provider="fake", out_dir=str(fake / "a")))
    b = json.loads(insert_image(str(src), "x", slide=3, provider="fake", out_dir=str(fake / "b")))
    assert Path(a["path"]).read_bytes() == Path(b["path"]).read_bytes()


def test_docx_drawing_added_in_paragraph(fake: Path) -> None:
    src = FIXTURES / "meeting_1111.docx"
    out = json.loads(
        insert_image(str(src), "그림", after_paragraph=3, provider="fake", out_dir=str(fake / "o"))
    )
    with zipfile.ZipFile(out["path"]) as z:
        doc = z.read("word/document.xml").decode()
        rels = z.read("word/_rels/document.xml.rels").decode()
        assert doc.count("<w:drawing") == 1
        assert "media/image1.png" in rels and z.read("word/media/image1.png")
    assert out["out_of_scope_changes"] == 0


def test_hwp_paragraph_gets_picture(fake: Path) -> None:
    src = FIXTURES / "meeting_1111.hwp"
    out = json.loads(
        insert_image(str(src), "그림", after_paragraph=3, provider="fake", out_dir=str(fake / "o"))
    )
    assert Path(out["path"]).stat().st_size > src.stat().st_size


def test_hwp_without_paragraph_reports_error(fake: Path) -> None:
    out = json.loads(
        insert_image(str(FIXTURES / "meeting_1111.hwp"), "그림", provider="fake", out_dir=str(fake))
    )
    assert out["step"] == "scope"


@pytest.mark.parametrize(
    ("provider", "key"), [("openai", "OPENAI_API_KEY"), ("gemini", "GEMINI_API_KEY")]
)
def test_missing_key_disables_feature(
    provider: str, key: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    monkeypatch.delenv(key, raising=False)
    out = json.loads(
        insert_image(str(FIXTURES / "company_intro.pptx"), "하늘", slide=3, provider=provider)
    )
    assert out["disabled"] is True
    assert "이미지 생성이 꺼져 있어요" in out["message"]
    assert key in out["message"] and out["env"] == key
    with pytest.raises(ImageDisabled):
        generate_image(provider, "x")


def test_bad_inputs_return_errors(fake: Path) -> None:
    src = str(FIXTURES / "company_intro.pptx")
    assert json.loads(insert_image(src, "x", slide=9, provider="fake"))["step"] == "scope"
    assert json.loads(insert_image(src, "  ", slide=1, provider="fake"))["step"] == "input"
    assert json.loads(insert_image(src, "x", slide=1, provider="nope"))["step"] == "generate"
    assert json.loads(insert_image(str(fake / "none.pptx"), "x", slide=1))["step"] == "input"
