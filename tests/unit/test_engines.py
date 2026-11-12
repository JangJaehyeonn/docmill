"""엔진 어댑터 단위 테스트 (FR-02)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from docmill.engines.deckcraft import DeckCraftTool
from docmill.engines.gridcraft import GridCraftTool
from docmill.engines.paths import PathOutsideWorkdirError, resolve_inside
from docmill.engines.wordcraft import WordCraftTool

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_resolve_inside_accepts_relative_and_nested(tmp_path: Path) -> None:
    assert resolve_inside(tmp_path, "a/b.xlsx") == tmp_path.resolve() / "a" / "b.xlsx"
    assert resolve_inside(tmp_path, tmp_path) == tmp_path.resolve()


@pytest.mark.parametrize("bad", ["../x.xlsx", "/etc/passwd", "a/../../x"])
def test_resolve_inside_rejects_outside(tmp_path: Path, bad: str) -> None:
    with pytest.raises(PathOutsideWorkdirError):
        resolve_inside(tmp_path, bad)


def test_workdir_is_required() -> None:
    with pytest.raises(ValueError, match="작업 폴더"):
        GridCraftTool(workdir=None)


def test_missing_binary_message_names_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCMILL_WORDCRAFT_PATH", str(tmp_path / "nope"))
    with WordCraftTool(tmp_path) as tool:
        result = tool.execute("text.insert", {"text": "x"})
    assert not result.ok
    assert "wordcraft" in result.message
    assert "DOCMILL_WORDCRAFT_PATH" in result.message


def test_close_is_idempotent(tmp_path: Path) -> None:
    tool = DeckCraftTool(tmp_path)
    tool.close()
    tool.close()


@pytest.mark.engine
@pytest.mark.parametrize(
    ("cls", "binary", "env", "sample"),
    [
        (GridCraftTool, "gridcraft-cli", "DOCMILL_GRIDCRAFT_PATH", "sales_q3.xlsx"),
        (DeckCraftTool, "deckcraft-cli", "DOCMILL_DECKCRAFT_PATH", "company_intro.pptx"),
        (WordCraftTool, "wordcraft-cli", "DOCMILL_WORDCRAFT_PATH", "meeting_1111.docx"),
    ],
)
def test_open_render_save_with_real_engine(
    cls: type[GridCraftTool | DeckCraftTool | WordCraftTool],
    binary: str,
    env: str,
    sample: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exe = shutil.which(binary)
    if exe is None:
        pytest.skip(f"{binary} 없음")
    monkeypatch.setenv(env, exe)
    shutil.copy(FIXTURES / sample, tmp_path / sample)
    with cls(tmp_path) as tool:
        opened = tool.open(tmp_path / sample)
        assert opened.ok, opened.message
        rendered = tool.render(tmp_path / sample, tmp_path)
        assert rendered.ok != (cls is GridCraftTool), rendered.message  # GridCraft는 렌더 미지원
        saved = tool.save(tmp_path / f"out{Path(sample).suffix}")
        assert saved.ok, saved.message
    assert (tmp_path / f"out{Path(sample).suffix}").exists()
    if cls is not GridCraftTool:
        assert list(tmp_path.glob("*.png"))
