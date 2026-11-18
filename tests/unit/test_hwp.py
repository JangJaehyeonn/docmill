"""한글(hwp) 읽기 단위 테스트 (FR-35)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from docmill.engines import rhwp
from docmill.tools.hwp import HwpError, is_hwp, read_hwp_lines, read_hwp_paragraphs
from docmill.tools.preflight import check_empty_titles
from docmill.tools.review import ReviewError, read_segments

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "meeting_1111.hwp"
EMPTY_TITLE = Path(__file__).resolve().parents[1] / "fixtures" / "meeting_empty_title.hwp"


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


def test_reads_paragraph_styles() -> None:
    paragraphs = read_hwp_paragraphs(EMPTY_TITLE)
    third, fifth = paragraphs[2], paragraphs[4]
    assert (third["section"], third["paragraph"], third["style"], third["text"]) == (
        1,
        3,
        "개요 1",
        "논의 내용",
    )
    assert (fifth["style"], fifth["styleEnglish"], fifth["text"]) == ("개요 1", "Outline 1", "")


def test_preflight_finds_empty_hwp_heading_only() -> None:
    result = check_empty_titles(EMPTY_TITLE)
    assert result["status"] != "통과"
    assert [item["location"] for item in result["locations"]] == [{"section": 1, "paragraph": 5}]


def test_preflight_hwp_without_headings_passes() -> None:
    result = check_empty_titles(FIXTURE)
    assert result["status"] == "통과" and result["locations"] == []


def test_preflight_unreadable_hwp_reports_read_step(tmp_path: Path) -> None:
    broken = tmp_path / "broken.hwp"
    broken.write_bytes(b"not a hwp")
    with pytest.raises(ReviewError) as info:
        check_empty_titles(broken)
    assert info.value.step == "read"


@pytest.mark.parametrize(
    ("stdout", "mode"),
    [("null", "lines"), ("3", "lines"), ("null", "paragraphs"), ('[1, "a"]', "paragraphs")],
)
def test_unexpected_reader_output_is_failed_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str, mode: str
) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(rhwp.shutil, "which", lambda _: "node")
    monkeypatch.setattr(rhwp, "_core_dir", lambda: tmp_path)
    monkeypatch.setattr(rhwp.subprocess, "run", fake_run)
    result = rhwp.RhwpTool(tmp_path)._read(tmp_path / "input.hwp", mode)
    assert not result.ok and result.message == "한글 문서 읽기 결과를 해석하지 못했다"
