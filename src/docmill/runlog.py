"""도구 호출 실행 로그 (FR-16).

모든 도구 호출을 실행 ID와 함께 SQLite에 기록한다. 인자와 결과는 요약만 남기고,
큰 데이터와 비밀값은 저장하지 않는다. 진단 메시지는 stderr(logging)로만 보낸다.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import logging
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

DB_NAME = "docmill.db"
MAX_VALUE_LEN = 200
MAX_SUMMARY_LEN = 2000
REDACTED = "***"

_SECRET_KEY = re.compile(r"key|token|secret|passw|credential|auth|cookie", re.IGNORECASE)
_SECRET_VALUE = re.compile(
    r"""(?:["']?(?:api[_-]?key|token|secret|password|passwd)["']?\s*[=:]\s*)"""
    r"""(?:"[^"]*"|'[^']*'|[^\s,}]+)|sk-[A-Za-z0-9_\-]{8,}""",
    re.IGNORECASE,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_runs (
    run_id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    args_summary TEXT NOT NULL,
    result TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    ai_used INTEGER NOT NULL,
    started_at TEXT NOT NULL
)
"""

_F = TypeVar("_F", bound=Callable[..., Any])

_current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "docmill_run_id", default=None
)


def current_run_id() -> str | None:
    """지금 실행 중인 `logged` 도구 호출의 실행 ID. 로그 밖에서 부르면 `None`."""
    return _current_run_id.get()


def get_home(home: Path | None = None) -> Path:
    """로그를 저장할 폴더를 정한다. 인자 > `DOCMILL_HOME` > `~/.docmill` 순서."""
    if home is not None:
        return Path(home)
    env = os.environ.get("DOCMILL_HOME")
    return Path(env) if env else Path.home() / ".docmill"


def _connect(home: Path | None) -> sqlite3.Connection:
    folder = get_home(home)
    folder.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(folder / DB_NAME)
    con.row_factory = sqlite3.Row
    con.execute(_SCHEMA)
    return con


def _clip(text: str) -> str:
    text = _SECRET_VALUE.sub(REDACTED, text)
    if len(text) > MAX_VALUE_LEN:
        return f"{text[:MAX_VALUE_LEN]}...(총 {len(text)}자, 생략)"
    return text


def _summarize_value(value: Any) -> str:
    if isinstance(value, bytes | bytearray):
        return f"<bytes {len(value)}>"
    if isinstance(value, str | int | float | bool) or value is None:
        return _clip(str(value))
    if isinstance(value, Mapping | list | tuple | set | frozenset):
        return f"<{type(value).__name__} {len(value)}개>"
    return f"<{type(value).__name__}>"


def summarize_args(args: Mapping[str, Any]) -> str:
    """로그에 남길 인자 요약을 만든다. 비밀값은 가리고 큰 값은 줄인다."""
    parts = []
    for key in sorted(args):
        shown = REDACTED if _SECRET_KEY.search(key) else _summarize_value(args[key])
        parts.append(f"{key}={shown}")
    text = ", ".join(parts)
    return text if len(text) <= MAX_SUMMARY_LEN else text[:MAX_SUMMARY_LEN] + "...(생략)"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_run_id() -> str:
    """새 실행 ID를 발급한다."""
    return uuid.uuid4().hex


def record_run(
    *,
    run_id: str,
    tool: str,
    args_summary: str,
    result: str,
    status: str,
    duration_ms: int,
    ai_used: bool,
    started_at: str | None = None,
    home: Path | None = None,
) -> None:
    """호출 한 건을 로그에 기록한다. `started_at`은 호출 시작 시각(UTC ISO)이다."""
    started = started_at or _now()
    con = _connect(home)
    try:
        with con:
            con.execute(
                "INSERT INTO tool_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, tool, args_summary, result, status, duration_ms, int(ai_used), started),
            )
    finally:
        con.close()


def get_run(run_id: str, home: Path | None = None) -> dict[str, Any] | None:
    """실행 ID로 기록을 조회한다. 없으면 `None`."""
    con = _connect(home)
    try:
        row = con.execute("SELECT * FROM tool_runs WHERE run_id = ?", (run_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    record = dict(row)
    record["ai_used"] = bool(record["ai_used"])
    return record


def _safe_record(**kwargs: Any) -> None:
    try:
        record_run(**kwargs)
    except Exception:  # logging must never break a tool call
        logger.exception("실행 로그 기록 실패")


def _bind_args(
    fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except TypeError:
        return dict(kwargs)
    return dict(bound.arguments)


def logged(fn: _F, *, ai_used: bool = True) -> _F:
    """도구 함수를 감싸 호출마다 실행 로그를 남긴다. 예외는 기록 후 그대로 던진다."""
    name = fn.__name__

    def finish(
        call_args: dict[str, Any],
        start: float,
        started_at: str,
        run_id: str,
        result: str,
        status: str,
    ) -> None:
        _safe_record(
            run_id=run_id,
            tool=name,
            args_summary=summarize_args(call_args),
            result=_clip(result),
            status=status,
            duration_ms=int((time.perf_counter() - start) * 1000),
            ai_used=ai_used,
            started_at=started_at,
        )

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            run_id, start, started_at = new_run_id(), time.perf_counter(), _now()
            call_args = _bind_args(fn, args, kwargs)
            _current_run_id.set(run_id)

            async def done(result: str, status: str) -> None:
                # SQLite work runs in a worker thread so the event loop is not blocked.
                # shield: cancelling the caller must not drop the queued log write.
                await asyncio.shield(
                    asyncio.to_thread(finish, call_args, start, started_at, run_id, result, status)
                )

            try:
                value = await fn(*args, **kwargs)
            except asyncio.CancelledError:
                await done("CancelledError", "cancelled")
                raise
            except Exception as exc:
                await done(f"{type(exc).__name__}: {exc}", "error")
                raise
            await done(str(value), "ok")
            return value

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        run_id, start, started_at = new_run_id(), time.perf_counter(), _now()
        call_args = _bind_args(fn, args, kwargs)
        token = _current_run_id.set(run_id)
        try:
            value = fn(*args, **kwargs)
        except Exception as exc:
            finish(call_args, start, started_at, run_id, f"{type(exc).__name__}: {exc}", "error")
            raise
        finally:
            _current_run_id.reset(token)
        finish(call_args, start, started_at, run_id, str(value), "ok")
        return value

    return wrapper  # type: ignore[return-value]
