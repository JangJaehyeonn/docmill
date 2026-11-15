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

    def add(self, tool: str, args: dict[str, Any]) -> None:
        """성공한 호출 하나를 기록한다."""
        self.calls.append((tool, dict(args)))


def _is_success(value: Any) -> bool:
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return False
    return isinstance(data, dict) and "error" not in data


def recording(fn: _F, recorder: SessionRecorder) -> _F:
    """도구 함수를 감싸, 성공한 호출(결과에 error가 없음)만 recorder에 남긴다."""
    if fn.__name__ not in RECIPE_TOOLS:
        return fn

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        value = fn(*args, **kwargs)
        if _is_success(value):
            bound = inspect.signature(fn).bind_partial(*args, **kwargs)
            recorder.add(fn.__name__, dict(bound.arguments))
        return value

    return wrapper  # type: ignore[return-value]


def build_steps(calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """기록된 호출을 레시피 단계로 바꾼다. 입력 파일 경로는 `$변수`로 치환한다."""
    variables: dict[tuple[str, str], str] = {}  # (arg name, path) -> variable
    steps: list[dict[str, Any]] = []
    for tool, args in calls:
        stored: dict[str, Any] = {}
        for key in sorted(args):
            if key in DROPPED_ARGS:
                continue
            value = args[key]
            if key in INPUT_ARGS and isinstance(value, str):
                ident = (key, value)
                if ident not in variables:
                    same = sum(1 for name, _ in variables if name == key)
                    variables[ident] = f"${key}" if same == 0 else f"${key}{same + 1}"
                value = variables[ident]
            stored[key] = value
        steps.append({"tool": tool, "args": stored})
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
    steps = build_steps(recorder.calls)
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
