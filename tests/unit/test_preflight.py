import json
import zipfile
from pathlib import Path

from docmill.tools.preflight import mask, preflight

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(path: Path, paragraphs: list[tuple[str, str | None]]) -> Path:
    body = ""
    for text, style in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body += f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"
    xml = f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)
    return path


def _run(tmp_path: Path, paragraphs: list[tuple[str, str | None]]) -> dict[str, dict]:  # type: ignore[type-arg]
    out = json.loads(preflight(str(_docx(tmp_path / "a.docx", paragraphs))))
    return {c["name"]: c for c in out["checks"]}


def test_clean_document_passes_everything(tmp_path: Path) -> None:
    checks = _run(tmp_path, [("보고", "Heading1"), ("2026. 11. 16.(월) 회의", None)])
    assert {c["status"] for c in checks.values()} == {"통과"}


def test_weekday_mismatch_and_invalid_date(tmp_path: Path) -> None:
    checks = _run(tmp_path, [("2026. 11. 16.(화) 회의", None), ("2026-02-30(월)", None)])
    entries = checks["날짜 요일"]["locations"]
    assert checks["날짜 요일"]["status"] == "실패"
    assert [e["location"]["paragraph"] for e in entries] == [1, 2]
    assert entries[0]["reason"] == "월요일이어야 함" and entries[1]["reason"] == "없는 날짜"


def test_privacy_is_masked_and_classified(tmp_path: Path) -> None:
    text = "주민 900101-1234567 전화 010-1234-5678 계좌 110-123-456789 메일 kim@example.com"
    out = preflight(str(_docx(tmp_path / "p.docx", [(text, None)])))
    kinds = [e["kind"] for e in json.loads(out)["checks"][2]["locations"]]
    assert kinds == ["주민등록번호", "전화번호", "계좌번호", "이메일"]
    for raw in ("1234567", "1234-5678", "123-456789", "example.com"):
        assert raw not in out


def test_dates_are_not_mistaken_for_accounts(tmp_path: Path) -> None:
    assert _run(tmp_path, [("2026-11-16(월) 회의", None)])["개인정보"]["status"] == "통과"


def test_placeholders_warn(tmp_path: Path) -> None:
    checks = _run(tmp_path, [("담당 ○○○, 일정 TBD, [작성] 필요", None)])
    assert checks["자리표시자"]["status"] == "주의"
    assert [e["text"] for e in checks["자리표시자"]["locations"]] == ["○○○", "TBD", "[작성]"]


def test_empty_heading_warns(tmp_path: Path) -> None:
    checks = _run(tmp_path, [("", "Heading1"), ("본문", None)])
    assert checks["빈 제목"]["status"] == "주의"
    assert checks["빈 제목"]["locations"][0]["location"] == {"paragraph": 1}


def test_unreadable_documents_report_error(tmp_path: Path) -> None:
    assert json.loads(preflight(str(tmp_path / "none.docx")))["step"] == "open"
    bad = tmp_path / "bad.docx"
    bad.write_text("x")
    assert json.loads(preflight(str(bad)))["step"] == "read"


def test_numbers_check_skipped_without_source_and_failed_with_bad_source(tmp_path: Path) -> None:
    doc = str(_docx(tmp_path / "n.docx", [("본문", None)]))
    assert json.loads(preflight(doc))["checks"][0]["status"] == "통과"
    first = json.loads(preflight(doc, str(tmp_path / "x.xlsx"), "부서", "매출"))["checks"][0]
    assert first["status"] == "실패" and first["step"] == "document"


def test_mask_keeps_two_characters() -> None:
    assert mask("900101-1234567") == "90****-*******"
