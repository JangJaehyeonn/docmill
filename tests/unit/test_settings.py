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


def test_check_title_compares_spaced_title_whole() -> None:
    settings = UserSettings(name="김민수", title="대표 이사")
    right = check_title([_seg("기안자: 대표 이사 김민수")], settings)
    assert right["status"] == "통과"
    wrong = check_title([_seg("기안자: 전무 이사 김민수")], settings)
    assert wrong["status"] == "주의"
    assert wrong["locations"][0]["text"] == "전무 이사 김민수"


def test_check_title_ignores_longer_name() -> None:
    settings = UserSettings(name="이진", title="대리")
    result = check_title([_seg("기안자: 과장 이진우")], settings)
    assert result["status"] == "통과"
    assert check_title([_seg("기안자: 과장 이진")], settings)["status"] == "주의"


def test_check_title_masks_privacy_in_result() -> None:
    settings = UserSettings(name="김민수", title="대리")
    result = check_title([_seg("연락처 alice@example.com 김민수")], settings)
    assert result["status"] == "주의"
    assert "alice@example.com" not in json.dumps(result, ensure_ascii=False)
