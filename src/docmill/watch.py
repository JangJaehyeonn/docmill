"""감시 폴더에 새 파일이 들어오면 레시피를 자동 실행한다. FR-50."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

from docmill import runlog
from docmill.runner import run_recipe

logger = logging.getLogger(__name__)

POLL_SECONDS = 2.0


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _state_path(recipe: str, home: Path | None) -> Path:
    key = hashlib.sha256(recipe.encode("utf-8")).hexdigest()[:12]
    return runlog.get_home(home) / f"watch-{key}.json"


def _load_state(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _save_state(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(seen)), encoding="utf-8")


def process_file(recipe: str, file: Path, out_dir: Path, home: Path | None = None) -> bool:
    """파일 하나로 레시피를 실행하고 결과를 실행 기록에 남긴다. 성공하면 True.

    점검(preflight)에 실패하면 결과를 만들지 않고 실패 기록만 남긴다.
    """
    run_id = runlog.new_run_id()
    started = time.perf_counter()
    started_at = runlog._now()
    result = json.loads(run_recipe(recipe, str(file), str(out_dir)))
    ok = "error" not in result
    status = "ok"
    if not ok:
        status = "preflight_failed" if result.get("step") == "preflight" else "failed"
    runlog.record_run(
        run_id=run_id,
        tool="run_recipe",
        args_summary=runlog.summarize_args({"recipe": recipe, "input": str(file)}),
        result=runlog._clip(json.dumps(result, ensure_ascii=False)),
        status=status,
        duration_ms=int((time.perf_counter() - started) * 1000),
        ai_used=False,
        started_at=started_at,
        recipe=recipe,
        home=home,
    )
    return ok


def _stable(path: Path, sizes: dict[Path, int]) -> bool:
    """크기가 직전 점검과 같아야(복사가 끝나야) True."""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    previous = sizes.get(path)
    sizes[path] = size
    return previous == size


def scan_once(
    recipe: str,
    watch_dir: Path,
    out_dir: Path,
    sizes: dict[Path, int],
    seen: set[str],
    ext: str = ".xlsx",
    home: Path | None = None,
) -> int:
    """감시 폴더를 한 번 훑어 처리하지 않은 새 파일을 실행한다. 처리한 개수를 돌려준다."""
    handled = 0
    for file in sorted(watch_dir.iterdir()):
        if not file.is_file() or file.name.startswith((".", "~$")) or file.suffix.lower() != ext:
            continue
        if not _stable(file, sizes):
            continue
        try:
            digest = _digest(file)
        except OSError:
            continue
        if digest in seen:
            continue
        seen.add(digest)  # mark first: never process the same content twice
        try:
            process_file(recipe, file, out_dir, home)
        except Exception:  # one bad file must not stop the watcher
            logger.exception("감시 실행 실패: %s", file)
        handled += 1
    return handled


def watch(
    recipe: str,
    watch_dir: Path,
    out_dir: Path,
    ext: str = ".xlsx",
    home: Path | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    poll: float = POLL_SECONDS,
) -> None:
    """`should_stop`이 True가 될 때까지 폴더를 감시한다. 처리한 내용은 다시 하지 않는다."""
    state_file = _state_path(recipe, home)
    seen = _load_state(state_file)
    sizes: dict[Path, int] = {}
    while not should_stop():
        if scan_once(recipe, watch_dir, out_dir, sizes, seen, ext, home):
            _save_state(state_file, seen)
        time.sleep(poll)
