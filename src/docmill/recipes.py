"""레시피 저장 (FR-09).

같은 세션(서버 프로세스)에서 성공한 docmill 도구 호출과 인자를 모아 이름 붙여
`DOCMILL_HOME/recipes/<이름>.json`에 저장한다. 엔진 명령이 아니라 docmill 도구 호출을
저장하며, 입력 파일 경로는 `$변수`로 바꾼다.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from docmill.runlog import current_run_id, get_home, new_run_id

RECIPE_TOOLS = frozenset(
    {"excel_to_report_ppt", "doc_to_task_table", "apply_template", "verify_numbers"}
)
# Arguments that hold input file paths -> become `$name` variables.
INPUT_ARGS = ("input", "document", "source")
# Environment-dependent arguments are not stored; the runner supplies them.
DROPPED_ARGS = frozenset({"out_dir"})
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

_F = TypeVar("_F", bound=Callable[..., Any])


class RecipeError(Exception):
    """레시피 저장 중 문제가 생겼을 때 발생한다. `step`은 실패한 단계 이름이다."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


class SessionRecorder:
    """한 세션에서 성공한 업무 도구 호출을 순서대로 모은다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.outputs: list[str | None] = []  # result `path` of each call

    def add(self, tool: str, args: dict[str, Any], output: str | None = None) -> None:
        """성공한 호출 하나와 그 결과 산출물 경로를 기록한다."""
        self.calls.append((tool, dict(args)))
        self.outputs.append(output)


def _parse_success(value: Any) -> dict[str, Any] | None:
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return None
    return data if isinstance(data, dict) and "error" not in data else None


def recording(fn: _F, recorder: SessionRecorder) -> _F:
    """도구 함수를 감싸, 성공한 호출(결과에 error가 없음)만 recorder에 남긴다."""
    if fn.__name__ not in RECIPE_TOOLS:
        return fn

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        value = fn(*args, **kwargs)
        data = _parse_success(value)
        if data is not None:
            bound = inspect.signature(fn).bind_partial(*args, **kwargs)
            path = data.get("path")
            recorder.add(
                fn.__name__, dict(bound.arguments), path if isinstance(path, str) else None
            )
        return value

    return wrapper  # type: ignore[return-value]


def build_steps(
    calls: list[tuple[str, dict[str, Any]]], outputs: list[str | None] | None = None
) -> list[dict[str, Any]]:
    """기록된 호출을 레시피 단계로 바꾼다.

    입력 경로가 앞 단계 산출물이면 `$stepN`(N은 1부터 센 그 단계 번호)으로, 그 외에는 경로마다
    변수 하나(`$input`, `$input2`, ...)로 바꾼다. 같은 파일이면 인자 이름이 달라도 같은 변수다.
    """
    produced: dict[str, int] = {}  # output path -> step number that made it
    variables: dict[str, str] = {}  # path -> variable
    steps: list[dict[str, Any]] = []
    for number, (tool, args) in enumerate(calls, start=1):
        stored: dict[str, Any] = {}
        for key in sorted(args):
            if key in DROPPED_ARGS:
                continue
            value = args[key]
            if key in INPUT_ARGS and isinstance(value, str):
                if value in produced:
                    value = f"$step{produced[value]}"
                else:
                    if value not in variables:
                        count = len(variables) + 1
                        variables[value] = "$input" if count == 1 else f"$input{count}"
                    value = variables[value]
            stored[key] = value
        steps.append({"tool": tool, "args": stored})
        output = outputs[number - 1] if outputs and number <= len(outputs) else None
        if output is not None:
            produced[output] = number
    return steps


def recipes_dir(home: Path | None = None) -> Path:
    """레시피 저장 폴더."""
    return get_home(home) / "recipes"


def _validate_name(name: str) -> str:
    clean = name.strip()
    if not clean or clean in {".", ".."} or _BAD_NAME.search(clean):
        raise RecipeError("name", f"레시피 이름으로 쓸 수 없다: {name!r}")
    return clean


def save_session_recipe(recorder: SessionRecorder, name: str) -> dict[str, Any]:
    """세션의 성공한 호출을 `name` 레시피로 저장하고 name·steps·path를 돌려준다."""
    clean = _validate_name(name)
    steps = build_steps(recorder.calls, recorder.outputs)
    if not steps:
        raise RecipeError("steps", "저장할 성공한 도구 호출이 이 세션에 없다")
    folder = recipes_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{clean}.json"
    partial = target.with_name(f".{target.name}.part")
    try:
        text = json.dumps({"name": clean, "steps": steps}, ensure_ascii=False, indent=2)
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return {"name": clean, "steps": steps, "path": str(target)}


def make_save_recipe(recorder: SessionRecorder) -> Callable[[str], str]:
    """세션 recorder에 묶인 `save_recipe` 도구 함수를 만든다."""

    def save_recipe(name: str) -> str:
        """이 세션에서 성공한 도구 호출과 인자를 이름 붙여 레시피로 저장한다.

        입력 파일 경로는 `$input` 같은 변수로 바뀌고 실패한 호출은 빠진다. 결과는 JSON 문자열
        (name·steps·path·run_id)이며, 실패하면 error와 실패한 단계(step)를 담는다.
        """
        run_id = current_run_id() or new_run_id()
        result: dict[str, Any]
        try:
            result = save_session_recipe(recorder, name)
        except RecipeError as exc:
            result = {"error": exc.message, "step": exc.step}
        except Exception as exc:  # file failures must not kill the server
            result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected"}
        result["run_id"] = run_id
        return json.dumps(result, ensure_ascii=False)

    return save_recipe


def list_recipes() -> list[str]:
    """저장된 레시피 이름을 이름순으로 돌려준다."""
    folder = recipes_dir()
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.json"))


def _existing_path(name: str) -> Path:
    path = recipes_dir() / f"{_validate_name(name)}.json"
    if not path.is_file():
        raise RecipeError("name", f"레시피를 찾을 수 없다: {name!r}")
    return path


def rename_recipe(old: str, new: str) -> str:
    """레시피 `old`의 이름을 `new`로 바꾸고 새 이름을 돌려준다. 같은 이름이 있으면 실패한다."""
    source = _existing_path(old)
    clean = _validate_name(new)
    target = source.with_name(f"{clean}.json")
    if target.exists():
        raise RecipeError("name", f"같은 이름의 레시피가 이미 있다: {clean!r}")
    data = json.loads(source.read_text(encoding="utf-8"))
    data["name"] = clean
    partial = target.with_name(f".{target.name}.part")
    try:
        partial.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    source.unlink()
    return clean


def delete_recipe(name: str) -> None:
    """레시피 `name`을 삭제한다. 없으면 RecipeError."""
    _existing_path(name).unlink()
