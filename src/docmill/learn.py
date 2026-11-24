"""보고 배우는 자동화 (FR-49).

MCP 세션(studio 채팅 포함)에서 성공한 업무 도구 호출을 인자와 함께 실행 로그 DB에 남기고,
입력 파일만 다른 같은 순서의 작업이 3번 이상 반복되면 레시피로 저장하자고 제안한다.
제안을 수락하면 save_recipe와 같은 형식의 레시피가 된다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docmill.recipes import RecipeError, build_steps, write_recipe
from docmill.runlog import DB_NAME, get_home

logger = logging.getLogger(__name__)

MIN_REPEATS = 3
MAX_BLOCK = 5  # longest step sequence considered as one repeated task
MAX_CALLS = 500  # only the most recent calls are scanned
MESSAGE = "매번 하시네요, 레시피로 저장할까요?"

_SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS session_calls (
    session_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    output TEXT
)
""",
    """
CREATE TABLE IF NOT EXISTS learned_patterns (
    pattern TEXT PRIMARY KEY,
    recipe TEXT NOT NULL
)
""",
)

Call = tuple[str, dict[str, Any], str | None]


@dataclass(frozen=True)
class Suggestion:
    """반복된 작업 하나. `calls`는 처음 나온 반복의 실제 호출이다."""

    pattern: str
    count: int
    steps: list[dict[str, Any]]
    calls: list[Call]

    def to_dict(self) -> dict[str, Any]:
        """CLI·화면에 보일 요약(message·count·tools·steps)."""
        tools = [step["tool"] for step in self.steps]
        return {
            "message": f"{MESSAGE} ({' → '.join(tools)} 작업을 {self.count}번 반복했습니다)",
            "count": self.count,
            "tools": tools,
            "steps": self.steps,
        }


def _connect(home: Path | None) -> sqlite3.Connection:
    folder = get_home(home)
    folder.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(folder / DB_NAME)
    for statement in _SCHEMA:
        con.execute(statement)
    return con


def record_call(
    session_id: str, tool: str, args: dict[str, Any], output: str | None, home: Path | None = None
) -> None:
    """성공한 업무 도구 호출 하나를 세션 ID와 함께 남긴다."""
    con = _connect(home)
    try:
        with con:
            con.execute(
                "INSERT INTO session_calls (session_id, tool, args_json, output)"
                " VALUES (?, ?, ?, ?)",
                (session_id, tool, json.dumps(args, ensure_ascii=False, default=str), output),
            )
    finally:
        con.close()


def call_logger(session_id: str) -> Callable[[str, dict[str, Any], str | None], None]:
    """SessionRecorder에 넘길 기록 함수. 기록 실패는 도구 호출을 막지 않는다."""

    def log(tool: str, args: dict[str, Any], output: str | None) -> None:
        try:
            record_call(session_id, tool, args, output)
        except Exception:  # logging must never break a tool call
            logger.exception("반복 작업 기록 실패")

    return log


def _load(home: Path | None) -> tuple[list[list[Call]], set[str]]:
    con = _connect(home)
    try:
        rows = con.execute(
            "SELECT session_id, tool, args_json, output FROM"
            " (SELECT rowid AS r, * FROM session_calls ORDER BY rowid DESC LIMIT ?)"
            " ORDER BY r",
            (MAX_CALLS,),
        ).fetchall()
        accepted = {row[0] for row in con.execute("SELECT pattern FROM learned_patterns")}
    finally:
        con.close()
    sessions: dict[str, list[Call]] = {}
    for session_id, tool, args_json, output in rows:
        args = json.loads(args_json)
        if isinstance(args, dict):
            sessions.setdefault(session_id, []).append((tool, args, output))
    return list(sessions.values()), accepted


def _steps(block: list[Call]) -> list[dict[str, Any]]:
    return build_steps([(tool, args) for tool, args, _ in block], [out for _, _, out in block])


def _key(block: list[Call]) -> str:
    return json.dumps(_steps(block), ensure_ascii=False, sort_keys=True)


def _count(session: list[Call], k: int, pattern: str) -> int:
    """`session` 안에서 겹치지 않게 `pattern`이 나온 횟수."""
    found, i = 0, 0
    while i + k <= len(session):
        if _key(session[i : i + k]) == pattern:
            found, i = found + 1, i + k
        else:
            i += 1
    return found


def _periodic(block: list[Call]) -> bool:
    """더 짧은 작업이 되풀이된 것일 뿐인 블록인지."""
    k = len(block)
    for d in range(1, k):
        if k % d == 0:
            first = _key(block[:d])
            if all(_key(block[i : i + d]) == first for i in range(d, k, d)):
                return True
    return False


def _contains(outer: list[Call], inner: list[dict[str, Any]]) -> bool:
    """`outer` 호출의 어느 연속 구간을 다시 정규화했을 때 인자·연결까지 `inner`와 같은지."""
    n = len(inner)
    return any(_steps(outer[i : i + n]) == inner for i in range(len(outer) - n + 1))


def find_repeats(sessions: list[list[Call]], accepted: set[str] | None = None) -> list[Suggestion]:
    """입력 파일만 다른 같은 순서의 호출이 MIN_REPEATS번 이상 나온 작업을 찾는다.

    같은 입력이면 같은 결과를 내도록 많이 반복된 것, 긴 것, 패턴 문자열 순으로 정렬한다.
    """
    skip = accepted or set()
    first: dict[str, list[Call]] = {}
    for session in sessions:
        for k in range(1, min(MAX_BLOCK, len(session)) + 1):
            for i in range(len(session) - k + 1):
                block = session[i : i + k]
                first.setdefault(_key(block), block)
    found: list[Suggestion] = []
    for pattern, block in first.items():
        if pattern in skip or _periodic(block):
            continue
        count = sum(_count(s, len(block), pattern) for s in sessions)
        if count >= MIN_REPEATS:
            found.append(Suggestion(pattern, count, _steps(block), block))
    kept = [s for s in found if not _covered(s, found)]
    return sorted(kept, key=lambda s: (-s.count, -len(s.steps), s.pattern))


def _covered(item: Suggestion, others: list[Suggestion]) -> bool:
    """같은 만큼 이상 반복된 더 긴 작업의 일부이면 따로 제안하지 않는다."""
    return any(
        len(o.steps) > len(item.steps) and o.count >= item.count and _contains(o.calls, item.steps)
        for o in others
    )


def _extra_inputs(steps: list[dict[str, Any]]) -> bool:
    """`$input2` 같은, run_recipe가 바인딩하지 못하는 입력 변수를 쓰는지."""
    return any(
        isinstance(v, str) and v.startswith("$input") and v != "$input"
        for step in steps
        for v in step["args"].values()
    )


def suggest(home: Path | None = None) -> list[Suggestion]:
    """저장된 호출 기록에서 레시피로 저장할 만한 반복 작업을 찾는다."""
    sessions, accepted = _load(home)
    return find_repeats(sessions, accepted)


def accept(name: str, pick: int = 1, home: Path | None = None) -> dict[str, Any]:
    """`pick`번째(1부터) 제안을 `name` 레시피로 저장하고 저장된 레시피를 돌려준다.

    수락한 작업은 다시 제안하지 않는다. 제안이 없거나 번호가 틀리면 RecipeError.
    """
    found = suggest(home)
    if not found:
        raise RecipeError("suggest", "저장할 반복 작업 제안이 없다")
    if not 1 <= pick <= len(found):
        raise RecipeError("suggest", f"제안 번호는 1~{len(found)} 사이여야 한다: {pick}")
    chosen = found[pick - 1]
    if _extra_inputs(chosen.steps):
        raise RecipeError("suggest", "입력 파일이 둘 이상인 작업은 run_recipe로 실행할 수 없다")
    saved = write_recipe(
        name, [(tool, args) for tool, args, _ in chosen.calls], [o for _, _, o in chosen.calls]
    )
    con = _connect(home)
    try:
        with con:
            con.execute(
                "INSERT OR REPLACE INTO learned_patterns (pattern, recipe) VALUES (?, ?)",
                (chosen.pattern, saved["name"]),
            )
    finally:
        con.close()
    return saved
