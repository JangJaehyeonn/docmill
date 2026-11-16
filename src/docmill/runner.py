"""레시피 실행 (FR-10).

저장된 레시피(`DOCMILL_HOME/recipes/<이름>.json`)의 단계를 순서대로 실행한다. 각 단계는
docmill 업무 도구 함수를 직접 호출하므로 AI를 쓰지 않으며, 같은 입력이면 같은 결과가 나온다.
`$input`에는 실행할 때 받은 입력 파일 경로가, `$stepN`에는 N번째 단계의 산출물 경로가 들어간다.
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
from docmill.tools.report import _read_sheet, excel_to_report_ppt
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


def bind_args(args: dict[str, Any], variables: dict[str, str], step: str) -> dict[str, Any]:
    """인자 값이 `$변수`이면 바인딩된 값으로 바꾼다.

    `$input`은 입력 파일, `$stepN`은 N번째 단계 산출물이다. 알 수 없는 변수는 `RecipeError`.
    """
    bound: dict[str, Any] = {}
    for key in sorted(args):
        value = args[key]
        if isinstance(value, str) and value.startswith("$"):
            if value not in variables:
                raise RecipeError(step, f"변수 {value}에 넣을 값이 없다")
            value = variables[value]
        bound[key] = value
    return bound


def _run_step(index: int, step: dict[str, Any], variables: dict[str, str]) -> dict[str, Any]:
    tool = step.get("tool")
    label = f"{index}:{tool}"
    runner = RUNNERS.get(tool) if isinstance(tool, str) else None
    if runner is None:
        raise RecipeError(label, f"실행할 수 없는 도구다: {tool!r}")
    args = bind_args(step.get("args", {}), variables, label)
    try:
        data = json.loads(runner(**args))
    except TypeError as exc:
        raise RecipeError(label, f"인자가 맞지 않는다: {exc}") from exc
    if not isinstance(data, dict) or "error" in data:
        message = data.get("error", "알 수 없는 결과") if isinstance(data, dict) else str(data)
        raise RecipeError(f"{label}({data.get('step', '?')})", str(message))
    return data


# Tools that read the Excel source, and the argument that names it.
SOURCE_ARG = {"excel_to_report_ppt": "input", "verify_numbers": "source"}


def required_columns(steps: list[dict[str, Any]]) -> list[str]:
    """`$input`을 엑셀 원본으로 쓰는 보고서·검증 단계가 요구하는 열 이름을 순서대로 모은다."""
    needed: list[str] = []
    for step in steps:
        args = step.get("args", {})
        source_key = SOURCE_ARG.get(str(step.get("tool")))
        if source_key is None or args.get(source_key) != "$input":
            continue
        for key in ("group_by", "value"):
            name = args.get(key)
            if isinstance(name, str) and name not in needed:
                needed.append(name)
    return needed


def preflight(steps: list[dict[str, Any]], input: str) -> None:
    """실행 전에 입력 엑셀에 레시피가 쓰는 열이 모두 있는지 확인한다.

    빠진 열이 있으면 아무 단계도 실행하지 않고 `RecipeError("preflight", ...)`를 낸다.
    """
    needed = required_columns(steps)
    source = Path(input)
    if not needed or source.suffix.lower() != ".xlsx" or not source.is_file():
        return  # nothing to check; the step itself reports unreadable input
    with tempfile.TemporaryDirectory(prefix="docmill-preflight-") as tmp:
        rows = _read_sheet(source, Path(tmp).resolve())
    header = [str(c).strip() if c is not None else "" for c in (rows[0] if rows else [])]
    missing = [name for name in needed if name not in header]
    if missing:
        names = ", ".join(f"'{name}'" for name in missing)
        raise RecipeError(
            "preflight", f"입력 파일에 없는 열: {names} (있는 열: {', '.join(header)})"
        )


FORCE_VERIFY_FAIL_ENV = "DOCMILL_TEST_FORCE_VERIFY_FAIL"


def verify_outputs(steps: list[dict[str, Any]], variables: dict[str, str], document: str) -> None:
    """최종 산출물을 만든 보고서 단계의 원본 엑셀 합계와 그래프 숫자를 대조한다 (FR-12).

    불일치나 검증 오류가 있으면 `RecipeError("verify", 원인)`을 낸다.
    환경 변수 `DOCMILL_TEST_FORCE_VERIFY_FAIL=1`이면 테스트용으로 불일치를 강제한다.
    """
    if Path(document).suffix.lower() != ".pptx":
        return
    produced_by = max(
        (i for i in range(1, len(steps) + 1) if variables.get(f"$step{i}") == document),
        default=len(steps),
    )
    # Only the last report step up to the one that produced the document applies to it.
    report_steps = [
        (index, step)
        for index, step in enumerate(steps[:produced_by], start=1)
        if step.get("tool") == "excel_to_report_ppt"
    ]
    for index, step in report_steps[-1:]:
        args = bind_args(step.get("args", {}), variables, f"{index}:excel_to_report_ppt")
        if not all(isinstance(args.get(k), str) for k in ("input", "group_by", "value")):
            continue  # nothing to compare against
        data = json.loads(verify_numbers(document, args["input"], args["group_by"], args["value"]))
        if "error" in data:
            raise RecipeError("verify", f"검증 실패: {data['error']}")
        mismatches = data["mismatches"]
        if os.environ.get(FORCE_VERIFY_FAIL_ENV) == "1":
            mismatches = [*mismatches, {"location": "forced", "expected": 0, "actual": 1}]
        if mismatches:
            first = mismatches[0]
            raise RecipeError(
                "verify",
                f"검증 불일치 {len(mismatches)}건 (예: {first['location']} "
                f"기대 {first['expected']} / 실제 {first['actual']})",
            )


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
    preflight(steps, input)
    variables = {"$input": input}
    results: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        result = _run_step(index, step, variables)
        results.append(result)
        if isinstance(result.get("path"), str):
            variables[f"$step{index}"] = result["path"]
    produced = [r["path"] for r in results if isinstance(r.get("path"), str)]
    if not produced:
        raise RecipeError("output", "레시피가 산출물을 만들지 않았다")
    verify_outputs(steps, variables, produced[-1])
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
