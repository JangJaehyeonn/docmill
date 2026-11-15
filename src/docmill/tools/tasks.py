"""`doc_to_task_table` 업무 도구 (FR-06).

워드 문서에서 `할 일: … / 담당: … / 마감: YYYY-MM-DD` 형식의 항목을 추출해 마감일 순 xlsx로
저장한다. 담당자나 마감일이 없는 항목은 이유와 함께 제외 목록으로 돌려준다. 추출·정렬은
입력만으로 정해지며(시각·난수 없음), 엔진은 `DocumentTool` 어댑터로만 호출한다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from docmill.engines import get_tool
from docmill.engines.base import DirectToolCaller, EngineResult
from docmill.runlog import current_run_id, get_home, new_run_id

HEADERS = ["할 일", "담당자", "마감일", "상태"]
DEFAULT_STATUS = "대기"
_FIELD = re.compile(r"^\s*(할 일|담당|마감)\s*[:：]\s*(.*?)\s*$")


class TaskError(Exception):
    """작업 단계 하나가 실패했을 때 발생한다. `step`은 실패한 단계 이름이다."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


@dataclass(frozen=True)
class Task:
    """문서에서 뽑은 항목 하나. 빠진 값은 빈 문자열이다."""

    item: str
    owner: str
    due: str


def _engine_json(result: EngineResult, step: str) -> dict[str, Any]:
    if not result.ok:
        raise TaskError(step, result.message)
    try:
        data = json.loads(result.output) if isinstance(result.output, str) else result.output
    except ValueError as exc:
        raise TaskError(step, f"엔진 응답을 해석하지 못했다: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _call(engine: DirectToolCaller, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _engine_json(engine.call_tool(tool, arguments), tool)


def parse_tasks(text: str) -> list[Task]:
    """본문에서 `할 일: … / 담당: … / 마감: …` 줄을 문서 순서대로 뽑는다."""
    tasks: list[Task] = []
    for line in text.splitlines():
        matches = [_FIELD.match(part) for part in line.split(" / ")]
        first = matches[0]
        if first is None or first.group(1) != "할 일":
            continue
        fields = {m.group(1): m.group(2) for m in matches if m}
        tasks.append(Task(fields["할 일"], fields.get("담당", ""), fields.get("마감", "")))
    return tasks


def _valid_date(raw: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return False
    try:
        date.fromisoformat(raw)
    except ValueError:
        return False
    return True


def split_tasks(tasks: list[Task]) -> tuple[list[Task], list[dict[str, str]]]:
    """완전한 항목(마감일 순, 같으면 문서 순)과 제외 목록(항목·이유)으로 나눈다."""
    kept: list[Task] = []
    excluded: list[dict[str, str]] = []
    for task in tasks:
        reasons = []
        if not task.item:
            reasons.append("할 일 내용이 없음")
        if not task.owner:
            reasons.append("담당자가 없음")
        if not task.due:
            reasons.append("마감일이 없음")
        elif not _valid_date(task.due):
            reasons.append(f"마감일 형식이 올바르지 않음 ({task.due})")
        if reasons:
            excluded.append({"item": task.item or "(내용 없음)", "reason": ", ".join(reasons)})
        else:
            kept.append(task)
    kept.sort(key=lambda t: t.due)  # sort is stable: ties keep document order
    return kept, excluded


def _read_text(source: Path, workdir: Path) -> str:
    staged = workdir / "input.docx"
    shutil.copyfile(source, staged)
    with get_tool(staged, workdir) as word:
        _engine_json(word.open(staged), "open_document")
        data = _call(word, "get_text", {})
    text = data.get("text")
    return text if isinstance(text, str) else ""


def _write_table(kept: list[Task], target: Path, workdir: Path) -> None:
    rows = [HEADERS] + [[t.item, t.owner, t.due, DEFAULT_STATUS] for t in kept]
    with get_tool(target, workdir) as grid:
        _call(grid, "new_workbook", {})
        _call(grid, "write_range", {"range": "A1", "values": rows})
        _engine_json(grid.save(target), "save_workbook")


def _publish(built: Path, final: Path) -> None:
    """산출물을 임시 파일로 복사한 뒤 원자적으로 교체한다. 실패하면 임시 파일을 지운다."""
    partial = final.with_name(f".{final.name}.part")
    try:
        shutil.copyfile(built, partial)
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _generate(input: str, out_dir: str | None, run_id: str) -> dict[str, Any]:
    source = Path(input)
    if source.suffix.lower() != ".docx" or not source.is_file():
        raise TaskError("input", f"읽을 수 있는 .docx 파일이 아니다: {input}")
    with tempfile.TemporaryDirectory(prefix="docmill-tasks-") as tmp:
        workdir = Path(tmp).resolve()
        tasks = parse_tasks(_read_text(source, workdir))
        if not tasks:
            raise TaskError("extract", "문서에서 '할 일: … / 담당: … / 마감: …' 항목을 찾지 못했다")
        kept, excluded = split_tasks(tasks)
        if not kept:
            raise TaskError("extract", "담당자와 마감일이 모두 있는 항목이 없다")
        built = workdir / "tasks.xlsx"
        _write_table(kept, built, workdir)
        target_dir = Path(out_dir) if out_dir else get_home() / "outputs"
        target_dir.mkdir(parents=True, exist_ok=True)
        final = target_dir / f"{run_id}.xlsx"
        _publish(built, final)  # publish only after everything else succeeded
    return {
        "path": str(final),
        "summary": f"할 일 {len(kept)}개를 마감일 순으로 저장, {len(excluded)}개 제외",
        "run_id": run_id,
        "tasks": [{"item": t.item, "owner": t.owner, "due": t.due} for t in kept],
        "excluded": excluded,
    }


def doc_to_task_table(input: str, out_dir: str | None = None) -> str:
    """워드 문서에서 할 일·담당자·마감일을 뽑아 마감일 순 xlsx로 저장한다.

    입력 형식은 `할 일: … / 담당: … / 마감: YYYY-MM-DD`이다. 담당자나 마감일이 없는 항목은
    표에 넣지 않고 결과의 excluded(item·reason)에 돌려준다. out_dir을 주지 않으면
    DOCMILL_HOME/outputs에 저장한다. 결과는 JSON 문자열이며, 실패하면 error와 실패한
    단계(step)를 담는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        result = _generate(input, out_dir, run_id)
    except TaskError as exc:
        result = {"error": exc.message, "step": exc.step, "run_id": run_id}
    except Exception as exc:  # engine or file failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected", "run_id": run_id}
    return json.dumps(result, ensure_ascii=False)
