"""한글(hwp) 읽기 단위 테스트 (FR-35)."""

from __future__ import annotations

from pathlib import Path

import pytest

from docmill.engines import rhwp
from docmill.tools.hwp import HwpError, is_hwp, read_hwp_lines
from docmill.tools.review import ReviewError, read_segments

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "meeting_1111.hwp"


def test_is_hwp_by_suffix() -> None:
    assert is_hwp(Path("a.HWP")) and is_hwp(Path("a.hwpx"))
    assert not is_hwp(Path("a.docx"))


def test_reads_paragraphs_in_order() -> None:
    lines = read_hwp_lines(FIXTURE)
    assert lines[0].startswith("주간 회의록")
    assert any(line.startswith("할 일: 3분기 매출 보고서") for line in lines)


def test_unreadable_hwp_raises(tmp_path: Path) -> None:
    broken = tmp_path / "broken.hwp"
    broken.write_bytes(b"not a hwp")
    with pytest.raises(HwpError):
        read_hwp_lines(broken)
    with pytest.raises(ReviewError) as info:
        read_segments(broken)
    assert info.value.step == "read"


def test_segments_skip_empty_lines() -> None:
    segments = read_segments(FIXTURE)
    assert segments and all(s.text for s in segments)


def test_relative_core_override_is_resolved_to_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = tmp_path / "core"
    core.mkdir()
    (core / "rhwp.js").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOCMILL_RHWP_CORE", "core")
    found = rhwp._core_dir()
    assert found is not None and found.is_absolute() and found == core.resolve()
