"""`apply_template` 업무 도구 (FR-07).

등록된 양식(FR-18)의 제목 규칙(크기·색·정렬·글꼴)을 PPT의 모든 슬라이드 제목에 적용한다.
본문은 건드리지 않으며, 같은 입력이면 같은 결과를 낸다. 엔진은 `DocumentTool` 어댑터로만 쓴다.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from docmill import templates
from docmill.engines import get_tool
from docmill.engines.mcp_tool import McpEngineTool
from docmill.runlog import current_run_id, get_home, new_run_id, output_stem
from docmill.templates import TITLE_TYPES, TemplateError


class ApplyError(Exception):
    """작업 단계 하나가 실패했을 때 발생한다. `step`은 실패한 단계 이름이다."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


def _command(engine: McpEngineTool, command: str, params: dict[str, Any]) -> Any:
    result = engine.execute(command, params)
    if not result.ok:
        raise ApplyError(command, result.message)
    out = result.output
    if isinstance(out, dict) and set(out) == {"result"}:
        out = out["result"]  # structured output may wrap the payload
    if isinstance(out, str):
        try:
            return json.loads(out)
        except ValueError:
            return out
    return out


def describe_rules(rules: dict[str, Any]) -> list[str]:
    """적용할 규칙을 사람이 읽을 목록으로 만든다 (고정 순서). 워드 양식은 수준별로 나열한다."""
    if rules.get("kind") == "docx":
        lines: list[str] = []
        for level, level_rules in sorted(rules.get("headings", {}).items()):
            lines.extend(f"제목 {level} · {line}" for line in _describe_flat(level_rules))
        return lines
    return _describe_flat(rules)


def _describe_flat(rules: dict[str, Any]) -> list[str]:
    labels = (
        ("title_size", "제목 크기", "{}pt"),
        ("title_color", "제목 색", "#{}"),
        ("title_align", "제목 정렬", "{}"),
        ("font", "제목 글꼴", "{}"),
    )
    return [f"{label}: {fmt.format(rules[key])}" for key, label, fmt in labels if rules.get(key)]


def _font_params(rules: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if rules.get("title_size"):
        params["size"] = rules["title_size"]
    if rules.get("title_color"):
        params["color"] = f"#{rules['title_color']}"
    if rules.get("font"):
        params["family"] = rules["font"]
    return params


def _format_title(engine: McpEngineTool, shape_id: int, rules: dict[str, Any]) -> None:
    _command(engine, "edit.select", {"ids": [shape_id]})
    if params := _font_params(rules):
        _command(engine, "format.fontDialog", params)
    if rules.get("title_align"):
        _command(engine, "format.align", {"align": rules["title_align"]})


def _title_ids(engine: McpEngineTool, index: int) -> list[int]:
    data = _command(engine, "slide.inspect", {"index": index})
    shapes = data.get("shapes", []) if isinstance(data, dict) else []
    return _collect_titles(shapes)


def _collect_titles(shapes: list[Any]) -> list[int]:
    """그룹 안(`children`)까지 내려가며 제목 도형의 ID만 모은다."""
    ids: list[int] = []
    for shape in shapes:
        if shape.get("placeholder") in TITLE_TYPES:
            ids.append(int(shape["id"]))
        ids.extend(_collect_titles(shape.get("children") or []))
    return ids


def _apply(engine: McpEngineTool, rules: dict[str, Any]) -> int:
    info = _command(engine, "file.info", {})
    slides = int(info.get("slides", 0)) if isinstance(info, dict) else 0
    modified = 0
    for index in range(slides):
        ids = _title_ids(engine, index)
        if not ids:
            continue
        _command(engine, "slide.go", {"index": index})
        for shape_id in ids:
            _format_title(engine, shape_id, rules)
        modified += 1
    return modified


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _word_heading_positions(path: Path) -> list[tuple[int, str]]:
    """본문 문단 중 제목 1~3 문단의 (1부터 세는 문단 번호, 수준)을 문서 순서로 돌려준다."""
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    body = root.find(f"{_W}body")
    found: list[tuple[int, str]] = []
    paragraphs = [] if body is None else [c for c in body if c.tag == f"{_W}p"]
    for number, p in enumerate(paragraphs, start=1):
        style = p.find(f"{_W}pPr/{_W}pStyle")
        level = templates.heading_level("" if style is None else style.get(f"{_W}val", ""))
        if level:
            found.append((number, level))
    return found


def _format_heading(engine: McpEngineTool, number: int, rules: dict[str, Any]) -> None:
    _command(engine, "edit.goto", {"paragraph": number})
    _command(engine, "select.paragraph", {})
    if rules.get("title_size"):
        _command(engine, "format.size", {"size": rules["title_size"]})
    if rules.get("title_color"):
        _command(engine, "format.color", {"color": rules["title_color"]})
    if rules.get("font"):
        _command(engine, "format.font", {"name": rules["font"]})
    if rules.get("title_align"):
        _command(engine, "para.align", {"value": rules["title_align"]})


def _apply_word(engine: McpEngineTool, staged: Path, rules: dict[str, Any]) -> int:
    levels: dict[str, dict[str, Any]] = rules.get("headings", {})
    modified = 0
    for number, level in _word_heading_positions(staged):
        if level in levels:
            _format_heading(engine, number, levels[level])
            modified += 1
    return modified


def _publish(built: Path, final: Path) -> None:
    """산출물을 임시 파일로 복사한 뒤 원자적으로 교체한다. 실패하면 임시 파일을 지운다."""
    partial = final.with_name(f".{final.name}.part")
    try:
        shutil.copyfile(built, partial)
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _load_rules(template: str, word: bool) -> tuple[dict[str, Any], list[str]]:
    try:
        rules = templates.load_template(template)
    except TemplateError as exc:
        raise ApplyError("template", str(exc)) from exc
    if (rules.get("kind") == "docx") != word:
        kind = ".docx" if word else ".pptx"
        raise ApplyError("template", f"양식 '{template}'은 {kind} 문서에 쓸 수 없는 종류다")
    applied = describe_rules(rules)
    if not applied:
        raise ApplyError("template", f"양식에 적용할 규칙이 없다: {template}")
    return rules, applied


def _run_engine(staged: Path, built: Path, rules: dict[str, Any], word: bool) -> int:
    """`staged`에 규칙을 적용해 `built`로 저장하고, 고친 수(슬라이드·제목)를 돌려준다."""
    open_step, save_step = (
        ("open_document", "save_document")
        if word
        else (
            "open_presentation",
            "save_presentation",
        )
    )
    with get_tool(staged, staged.parent) as engine:
        opened = engine.open(staged)
        if not opened.ok:
            raise ApplyError(open_step, opened.message)
        modified = _apply_word(engine, staged, rules) if word else _apply(engine, rules)
        saved = engine.save(built)
        if not saved.ok:
            raise ApplyError(save_step, saved.message)
    return modified


def _generate(input: str, template: str, out_dir: str | None, run_id: str) -> dict[str, Any]:
    source = Path(input)
    suffix = source.suffix.lower()
    if suffix not in (".pptx", ".docx") or not source.is_file():
        raise ApplyError("input", f"읽을 수 있는 .pptx 또는 .docx 파일이 아니다: {input}")
    word = suffix == ".docx"
    rules, applied = _load_rules(template, word)
    with tempfile.TemporaryDirectory(prefix="docmill-template-") as tmp:
        workdir = Path(tmp).resolve()
        staged = workdir / f"input{suffix}"
        shutil.copyfile(source, staged)
        built = workdir / f"result{suffix}"
        modified = _run_engine(staged, built, rules, word)
        target_dir = Path(out_dir) if out_dir else get_home() / "outputs"
        target_dir.mkdir(parents=True, exist_ok=True)
        final = target_dir / f"{output_stem(run_id)}{suffix}"
        _publish(built, final)  # publish only after everything else succeeded
    unit = "개 제목" if word else "개 슬라이드의 제목"
    return {
        "path": str(final),
        "summary": f"{modified}{unit}에 양식 '{template}'을 적용",
        "run_id": run_id,
        "pages_modified": modified,
        "rules": applied,
    }


def apply_template(input: str, template: str, out_dir: str | None = None) -> str:
    """등록된 양식의 제목 규칙(크기·색·정렬·글꼴)을 PPT 슬라이드 제목이나 워드 제목 1~3에 적용한다.

    template은 등록한 양식 이름이다. 본문 내용은 바꾸지 않는다. out_dir을 주지 않으면
    DOCMILL_HOME/outputs에 저장한다. 결과는 JSON 문자열(path·pages_modified·rules·run_id)이며,
    실패하면 error와 실패한 단계(step)를 담는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        result = _generate(input, template, out_dir, run_id)
    except ApplyError as exc:
        result = {"error": exc.message, "step": exc.step, "run_id": run_id}
    except Exception as exc:  # engine or file failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected", "run_id": run_id}
    return json.dumps(result, ensure_ascii=False)
