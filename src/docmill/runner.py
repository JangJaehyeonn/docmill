"""레시피 실행 (FR-10).

저장된 레시피(`DOCMILL_HOME/recipes/<이름>.json`)의 단계를 순서대로 실행한다. 각 단계는
docmill 업무 도구 함수를 직접 호출하므로 AI를 쓰지 않으며, 같은 입력이면 같은 결과가 나온다.
`$변수`에는 실행할 때 받은 입력 파일 경로가 들어간다.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from docmill.recipes import RECIPE_TOOLS, RecipeError, recipes_dir
from docmill.runlog import current_run_id, new_run_id
from docmill.tools.report import excel_to_report_ppt
from docmill.tools.tasks import doc_to_task_table
from docmill.tools.template import apply_template
from docmill.tools.verify import verify_numbers

# Tool functions a recipe may replay (called directly, never through an AI).
RUNNERS: dict[str, Callable[..., str]] = {
    "excel_to_report_ppt": excel_to_report_ppt,
    "doc_to_task_table": doc_to_task_table,
    "apply_template": apply_template,
    "verify_numbers": verify_numbers,
}
assert set(RUNNERS) == RECIPE_TOOLS


def load_recipe(recipe: str) -> list[dict[str, Any]]:
    """저장된 레시피의 단계 목록을 읽는다. 없거나 깨졌으면 `RecipeError`."""
    path = recipes_dir() / f"{recipe}.json"
    if not recipe.strip() or path.parent != recipes_dir() or not path.is_file():
        raise RecipeError("recipe", f"저장된 레시피가 없다: {recipe!r}")
    try:
        steps = json.loads(path.read_text(encoding="utf-8"))["steps"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RecipeError("recipe", f"레시피 파일을 읽을 수 없다: {recipe!r} ({exc})") from exc
    if not isinstance(steps, list) or not steps:
        raise RecipeError("recipe", f"레시피에 실행할 단계가 없다: {recipe!r}")
    return steps


def bind_args(
    args: dict[str, Any], variables: dict[str, str], step: str, previous: str | None = None
) -> dict[str, Any]:
    """인자 값이 `$변수`이면 입력 값으로 바꾼다.

    `$input` 외의 변수는 앞 단계가 만든 산출물(previous)로 바꾼다. 앞 단계가 없으면 `RecipeError`.
    """
    bound: dict[str, Any] = {}
    for key in sorted(args):
        value = args[key]
        if isinstance(value, str) and value.startswith("$"):
            if value in variables:
                value = variables[value]
            elif previous is not None:
                value = previous
            else:
                raise RecipeError(step, f"변수 {value}에 넣을 값이 없다")
        bound[key] = value
    return bound


def _run_step(
    index: int, step: dict[str, Any], variables: dict[str, str], previous: str | None = None
) -> dict[str, Any]:
    tool = step.get("tool")
    label = f"{index}:{tool}"
    runner = RUNNERS.get(tool) if isinstance(tool, str) else None
    if runner is None:
        raise RecipeError(label, f"실행할 수 없는 도구다: {tool!r}")
    args = bind_args(step.get("args", {}), variables, label, previous)
    try:
        data = json.loads(runner(**args))
    except TypeError as exc:
        raise RecipeError(label, f"인자가 맞지 않는다: {exc}") from exc
    if not isinstance(data, dict) or "error" in data:
        message = data.get("error", "알 수 없는 결과") if isinstance(data, dict) else str(data)
        raise RecipeError(f"{label}({data.get('step', '?')})", str(message))
    return data


def _deliver(path: str, out_dir: str, recipe: str) -> str:
    """산출물을 out_dir에 `<레시피>.<확장자>`로 원자적으로 복사한다."""
    source = Path(path)
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{recipe}{source.suffix}"
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=folder)
    os.close(fd)
    partial = Path(name)
    try:
        shutil.copyfile(source, partial)
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return str(target)


def execute_recipe(recipe: str, input: str, out_dir: str | None) -> dict[str, Any]:
    """레시피 단계를 순서대로 실행하고 마지막 산출물 path와 단계별 결과를 돌려준다."""
    steps = load_recipe(recipe)
    variables = {"$input": input}
    results: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        previous = next(
            (r["path"] for r in reversed(results) if isinstance(r.get("path"), str)), None
        )
        results.append(_run_step(index, step, variables, previous))
    produced = [r["path"] for r in results if isinstance(r.get("path"), str)]
    if not produced:
        raise RecipeError("output", "레시피가 산출물을 만들지 않았다")
    path = _deliver(produced[-1], out_dir, recipe) if out_dir else produced[-1]
    return {
        "recipe": recipe,
        "path": path,
        "steps": [{"tool": s.get("tool"), "ok": True} for s in steps],
        "summary": results[-1].get("summary", ""),
        "ai_used": False,
    }


def run_recipe(recipe: str, input: str, out_dir: str | None = None) -> str:
    """저장된 레시피를 새 입력 파일로 AI 없이 순서대로 실행한다.

    recipe는 save_recipe로 저장한 이름, input은 레시피의 `$input`에 넣을 파일 경로, out_dir은
    결과를 복사할 폴더(선택)다. 결과는 JSON 문자열(path·steps·summary·run_id)이며, 실패하면
    error와 실패한 단계(step)를 담는다.
    """
    run_id = current_run_id() or new_run_id()
    result: dict[str, Any]
    try:
        result = execute_recipe(recipe, input, out_dir)
    except RecipeError as exc:
        result = {"error": exc.message, "step": exc.step}
    except Exception as exc:  # engine or file failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected"}
    result["run_id"] = run_id
    return json.dumps(result, ensure_ascii=False)
