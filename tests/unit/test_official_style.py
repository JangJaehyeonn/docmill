"""check_official_style 규칙 단위 테스트 (FR-38)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from docmill.tools.official_style import (
    AMOUNT,
    BULLET,
    DATE,
    END_MARK,
    TIME,
    apply_edits,
    check_official_style,
    find_violations,
    fix_docx_xml,
    korean_numeral,
    text_edits,
)
from docmill.tools.review import Segment


def _segments(lines: list[str]) -> list[Segment]:
    return [Segment("text", t, {"paragraph": i}) for i, t in enumerate(lines, start=1)]


def _fixed(text: str) -> str:
    return apply_edits(text, text_edits(text))


def test_korean_numeral() -> None:
    assert korean_numeral(10150) == "일만백오십"
    assert korean_numeral(113560) == "십일만삼천오백육십"
    assert korean_numeral(1000) == "천"
    assert korean_numeral(100_000_000) == "일억"
    assert korean_numeral(0) == "영"


def test_dates_are_normalized() -> None:
    assert _fixed("2026-11-16에") == "2026. 11. 16.에"
    assert _fixed("2026년 11월 7일") == "2026. 11. 7."
    assert _fixed("2026.11.16") == "2026. 11. 16."
    assert text_edits("2026. 11. 16.") == []


def test_times_are_normalized() -> None:
    assert _fixed("오후 2시 30분") == "14:30"
    assert _fixed("9시") == "09:00"
    assert _fixed("3시간") == "3시간"
    assert text_edits("14:30") == []


def test_amount_is_normalized_once() -> None:
    assert _fixed("10,150원") == "금10,150원(금일만백오십원)"
    assert _fixed("금10,150원") == "금10,150원(금일만백오십원)"
    assert text_edits("금10,150원(금일만백오십원)") == []
    assert text_edits("3원칙") == []


def test_bullet_order_and_end_mark() -> None:
    rules = [v["rule"] for v in find_violations(_segments(["1. 가", "1) 나", "끝."]))]
    assert rules == [BULLET]
    ok = find_violations(_segments(["1. 가", "가. 나", "1) 다", "본문  끝."]))
    assert ok == []
    missing = find_violations(_segments(["본문"]))
    assert [v["rule"] for v in missing] == [END_MARK]


def test_violations_cover_all_text_rules() -> None:
    found = find_violations(_segments(["2026-11-16 14시 10,000원  끝."]))
    assert [v["rule"] for v in found] == [DATE, TIME, AMOUNT]


def test_fix_docx_xml_edits_runs_and_adds_end_mark() -> None:
    xml = "<w:body><w:p><w:r><w:t>2026-11-16 &amp; 끝</w:t></w:r></w:p></w:body>"
    out, applied = fix_docx_xml(xml, add_end_mark=True)
    assert applied == 2
    assert 'xml:space="preserve"' in out
    assert "2026. 11. 16. &amp; 끝  끝.</w:t>" in out
    assert "끝." not in fix_docx_xml(xml, add_end_mark=False)[0]


def test_fix_keeps_nested_text_box_paragraph_separate() -> None:
    xml = (
        "<w:body><w:p><w:r><w:t>10</w:t></w:r><w:r><w:pict><w:txbxContent>"
        "<w:p><w:r><w:t>150원</w:t></w:r></w:p></w:txbxContent></w:pict></w:r></w:p></w:body>"
    )
    out, _ = fix_docx_xml(xml, add_end_mark=False)
    assert "<w:t>10</w:t>" in out
    assert "금150원(금백오십원)" in out


def test_amount_keeps_unrelated_note_and_big_amount_does_not_crash() -> None:
    assert _fixed("10,150원(금요일 마감, 담당 김원)") == (
        "금10,150원(금일만백오십원)(금요일 마감, 담당 김원)"
    )
    big = find_violations(_segments(["100,000,000,000,000,000,000원 2026-11-16"]))
    assert [v["rule"] for v in big] == [AMOUNT, DATE, END_MARK]
    assert "after" not in big[0]


def test_ampm_colon_time() -> None:
    assert _fixed("오후 2:30") == "14:30"
    assert _fixed("오전 9:05") == "09:05"


def test_same_level_bullets_out_of_order() -> None:
    found = find_violations(_segments(["2. 둘째", "1. 첫째", "끝."]))
    assert [v["rule"] for v in found] == [BULLET]
    restart = find_violations(_segments(["1. 가", "가. 나", "2. 다", "가. 라", "끝."]))
    assert restart == []


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read("word/document.xml").decode("utf-8")


def test_fix_handles_date_split_across_runs(tmp_path: Path) -> None:
    body = "<w:p><w:r><w:t>회의 2026-</w:t></w:r><w:r><w:t>11-16 개최</w:t></w:r></w:p>"
    xml = f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    source = tmp_path / "a.docx"
    with zipfile.ZipFile(source, "w") as z:
        z.writestr("word/document.xml", xml)
    original = source.read_bytes()
    out = json.loads(check_official_style(str(source), fix=True, out_dir=str(tmp_path / "out")))
    assert [v["rule"] for v in out["violations"]] == [DATE, END_MARK]
    assert out["fixed"] == 2
    fixed = _docx_text(Path(out["path"]))
    assert "회의 2026. 11. 16.</w:t>" in fixed
    assert "> 개최  끝.</w:t>" in fixed
    assert source.read_bytes() == original
    again = json.loads(check_official_style(out["path"]))
    assert again["violations"] == []
