import json
from pathlib import Path

from docmill.tools.suggest import suggest_actions

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _items(name: str) -> list[dict[str, str]]:
    return list(json.loads(suggest_actions(str(FIXTURES / name)))["suggestions"])


def test_meeting_docx_suggests_task_table_first() -> None:
    items = _items("meeting_1111.docx")
    assert 1 <= len(items) <= 3
    assert items[0]["tool"] == "doc_to_task_table"
    assert "할 일 표 만들기" in items[0]["label"]


def test_docx_without_task_lines_has_no_task_table() -> None:
    tools = [s["tool"] for s in _items("report_headings.docx")]
    assert "doc_to_task_table" not in tools


def test_xlsx_suggests_report_ppt() -> None:
    assert _items("sales_q3.xlsx")[0]["tool"] == "excel_to_report_ppt"


def test_same_file_same_suggestions() -> None:
    assert _items("meeting_1111.docx") == _items("meeting_1111.docx")


def test_unsupported_and_missing_files(tmp_path: Path) -> None:
    other = tmp_path / "a.txt"
    other.write_text("x", encoding="utf-8")
    assert json.loads(suggest_actions(str(other))) == {"suggestions": []}
    assert "error" in json.loads(suggest_actions(str(tmp_path / "none.docx")))
