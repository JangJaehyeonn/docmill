"""doc_to_task_table 단위 테스트 (FR-06)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docmill.tools.tasks import Task, doc_to_task_table, parse_tasks, split_tasks

TEXT = "\n".join(
    [
        "회의록",
        "할 일: B / 담당: 가 / 마감: 2026-11-20",
        "할 일: A / 담당: 나 / 마감: 2026-11-15",
        "할 일: C / 마감: 2026-12-01",
        "할 일: D / 담당: 다",
        "할 일: E / 담당: 라 / 마감: 내일",
        "담당: 무관한 줄",
    ]
)


def test_parse_tasks_reads_fields_in_document_order() -> None:
    tasks = parse_tasks(TEXT)
    assert [t.item for t in tasks] == ["B", "A", "C", "D", "E"]
    assert tasks[2] == Task("C", "", "2026-12-01")


def test_split_sorts_by_due_and_explains_exclusions() -> None:
    kept, excluded = split_tasks(parse_tasks(TEXT))
    assert [t.item for t in kept] == ["A", "B"]
    reasons = {e["item"]: e["reason"] for e in excluded}
    assert "담당자" in reasons["C"]
    assert "마감일이 없음" in reasons["D"]
    assert "형식" in reasons["E"]


def test_missing_file_reports_input_step(tmp_path: Path) -> None:
    result = json.loads(doc_to_task_table(str(tmp_path / "none.docx")))
    assert result["step"] == "input"
    assert result["run_id"]


def test_wrong_extension_rejected(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("x")
    assert json.loads(doc_to_task_table(str(source)))["step"] == "input"


def test_engine_failure_is_returned_not_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import docmill.tools.tasks as tasks

    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    source = tmp_path / "in.docx"
    source.write_bytes(b"x")

    def boom(*_a: object) -> str:
        raise RuntimeError("engine down")

    monkeypatch.setattr(tasks, "_read_text", boom)
    result = json.loads(doc_to_task_table(str(source)))
    assert result["step"] == "unexpected"
    assert "engine down" in result["error"]


def test_no_complete_items_fails_without_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import docmill.tools.tasks as tasks

    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    source = tmp_path / "in.docx"
    source.write_bytes(b"x")
    monkeypatch.setattr(tasks, "_read_text", lambda *_a: "할 일: D / 담당: 다")
    result = json.loads(doc_to_task_table(str(source)))
    assert result["step"] == "extract"
    assert not (tmp_path / "outputs").exists()
