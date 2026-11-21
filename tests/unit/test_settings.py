"""사용자 설정 읽기와 preflight 직함 점검 단위 테스트 (FR-45)."""

from __future__ import annotations

import json
from pathlib import Path

from docmill.settings import UserSettings, load_settings
from docmill.tools.preflight import check_title
from docmill.tools.review import Segment


def _write(ws: Path, data: object) -> None:
    (ws / ".docmill").mkdir(parents=True)
    (ws / ".docmill" / "settings.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def test_load_settings_reads_fields(tmp_path: Path) -> None:
    _write(tmp_path, {"department": " 영업팀 ", "approval_line": ["기안자", " ", "팀장"], "x": 1})
    loaded = load_settings(tmp_path)
    assert loaded == UserSettings(department="영업팀", approval_line=["기안자", "팀장"])


def test_load_settings_missing_or_broken(tmp_path: Path) -> None:
    assert load_settings(tmp_path) is None
    _write(tmp_path, ["not", "a", "dict"])
    assert load_settings(tmp_path) is None


def _seg(text: str) -> Segment:
    return Segment(kind="text", text=text, location={"paragraph": 1})


def test_check_title_warns_on_different_title() -> None:
    settings = UserSettings(name="김민수", title="대리")
    wrong = check_title([_seg("기안자: 과장 김민수")], settings)
    assert wrong["status"] == "주의" and wrong["locations"]
    right = check_title([_seg("기안자: 대리 김민수"), _seg("김민수")], settings)
    assert right["status"] == "통과"
