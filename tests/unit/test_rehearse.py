"""register_reviewer / rehearse_review 단위 테스트 (FR-43)."""

import json
import zipfile
from pathlib import Path

import pytest

from docmill.tools.rehearse import derive_rules, register_reviewer, rehearse_review

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _docx(path: Path, paragraphs: list[str], pages: int | None = None) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f"<w:document {_W}><w:body>{body}</w:body></w:document>")
        if pages:
            z.writestr(
                "docProps/app.xml",
                f'<Properties xmlns="x"><Pages>{pages}</Pages></Properties>',
            )
    return path


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path / "home"))


def test_length_rule_takes_number_from_comments() -> None:
    rules = derive_rules(["너무 길다", "한 장으로 줄여"])
    assert rules == [{"kind": "길이", "limit": 1, "source": "너무 길다"}]
    assert derive_rules(["3쪽 넘으면 너무 길어"])[0]["limit"] == 3


def test_register_and_rehearse_length(tmp_path: Path) -> None:
    out = json.loads(register_reviewer("김부장", ["너무 길다", "한 장으로 줄여"]))
    assert out["rules"][0]["limit"] == 1
    doc = _docx(tmp_path / "a.docx", ["가"], pages=4)
    res = json.loads(rehearse_review("김부장", str(doc)))
    assert [(i["reason"], i["current"]) for i in res["items"]] == [("길이 초과", 4)]
    ok = json.loads(rehearse_review("김부장", str(_docx(tmp_path / "b.docx", ["가"], pages=1))))
    assert ok["items"] == []


def test_evidence_rule_flags_unsourced_numbers(tmp_path: Path) -> None:
    register_reviewer("이과장", ["근거가 뭐야"])
    doc = _docx(tmp_path / "a.docx", ["매출은 9,999원"])
    res = json.loads(rehearse_review("이과장", str(doc)))
    assert [i["reason"] for i in res["items"]] == ["출처 없음"]


def test_conclusion_rule(tmp_path: Path) -> None:
    register_reviewer("박이사", ["결론이 뒤에 있어, 먼저 써"])
    texts = ["배경", "현황", "분석", "계획", "일정", "결론: 승인 요청"]
    res = json.loads(rehearse_review("박이사", str(_docx(tmp_path / "a.docx", texts))))
    assert [i["reason"] for i in res["items"]] == ["결론이 뒤에 있음"]
    front = _docx(tmp_path / "b.docx", ["결론: 승인 요청", *texts[:5]])
    assert json.loads(rehearse_review("박이사", str(front)))["items"] == []


def test_unknown_reviewer_and_comment_files(tmp_path: Path) -> None:
    res = json.loads(rehearse_review("박사장", str(_docx(tmp_path / "a.docx", ["가"]))))
    assert "error" not in res and "기준이 없어요" in res["message"]
    memo = tmp_path / "memo.txt"
    memo.write_text("두 장으로 줄여\n\n근거 알려줘\n", encoding="utf-8")
    out = json.loads(register_reviewer("최팀장", comments_file=str(memo)))
    assert [(r["kind"], r.get("limit")) for r in out["rules"]] == [("길이", 2), ("근거", None)]
    missing = json.loads(register_reviewer("최팀장", comments_file=str(tmp_path / "x.txt")))
    assert missing["step"] == "read"


def test_page_breaks_set_page_floor(tmp_path: Path) -> None:
    register_reviewer("정대리", ["한 장으로 줄여"])
    doc = tmp_path / "a.docx"
    br = '<w:r><w:br w:type="page"/></w:r>'
    body = br.join(f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in "가나다라")
    with zipfile.ZipFile(doc, "w") as z:
        z.writestr("word/document.xml", f"<w:document {_W}><w:body>{body}</w:body></w:document>")
    res = json.loads(rehearse_review("정대리", str(doc)))
    assert [(i["reason"], i["current"]) for i in res["items"]] == [("길이 초과", 4)]


def test_missing_conclusion_is_reported(tmp_path: Path) -> None:
    register_reviewer("한이사", ["결론을 먼저 써"])
    doc = _docx(tmp_path / "a.docx", ["배경", "현황", "계획"])
    res = json.loads(rehearse_review("한이사", str(doc)))
    assert [i["reason"] for i in res["items"]] == ["결론을 찾지 못함"]


def test_reregister_replaces_without_leftover_tmp(tmp_path: Path) -> None:
    register_reviewer("오과장", ["너무 길다"])
    out = json.loads(register_reviewer("오과장", ["근거가 뭐야"]))
    assert [r["kind"] for r in out["rules"]] == ["근거"]
    assert not list((tmp_path / "home" / "reviewers").glob("*.tmp"))
