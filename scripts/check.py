"""Claude Code Stop 훅: 작업을 끝내기 전에 품질 검사를 실행한다.

검사가 하나라도 실패하면 종료 코드 2를 돌려준다.
Claude Code는 종료 코드 2를 받으면 작업을 끝내지 않고 stderr 내용을 보고 계속 고친다.
같은 실패로 훅이 다시 불리면(stop_hook_active) 무한 반복을 막기 위해 경고만 남기고 끝낸다.
최종 관문은 CI이므로 여기서 놓친 실패는 PR에서 다시 걸린다.

인수 테스트 범위(DOCMILL_ACTIVE_FRS)가 정해져 있지 않으면 브랜치 이름 `claude/<이슈번호>-...`에서
이슈의 요구사항 ID를 찾아 '완료된 요구사항 + 이 이슈의 요구사항'으로 채운다.
Windows·macOS·Linux 모두에서 동작하도록 셸 스크립트 대신 Python으로 작성했다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from _gh import closed_frs, fr_of, gh_json, label_names

CHECKS: list[tuple[str, list[str]]] = [
    ("ruff check", ["uv", "run", "ruff", "check", "."]),
    ("ruff format", ["uv", "run", "ruff", "format", "--check", "."]),
    ("mypy", ["uv", "run", "mypy"]),
    ("pytest", ["uv", "run", "pytest", "-q", "-x"]),
]


def infer_active_frs() -> str | None:
    """현재 브랜치의 이슈에서 인수 테스트 범위를 추론한다. 알 수 없으면 None."""
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    match = re.match(r"^claude/(\d+)-", branch)
    if not match:
        return None
    try:
        issue = gh_json("issue", "view", match.group(1), "--json", "labels")
        fr = fr_of(label_names(issue))
        done = closed_frs()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"경고: 인수 테스트 범위를 계산하지 못했습니다 ({exc}).", file=sys.stderr)
        return None
    if fr:
        done.add(fr)
    return ",".join(sorted(done))


def _stop_hook_active() -> bool:
    """Claude Code가 이미 이 훅 때문에 한 번 계속 진행했는지 확인한다."""
    if sys.stdin is None or sys.stdin.isatty():
        return False
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("stop_hook_active") is True


def main() -> int:
    retry = _stop_hook_active()
    env = dict(os.environ)
    # Windows defaults to cp949; force UTF-8 for tools reading config and for captured output.
    env["PYTHONUTF8"] = "1"
    if not env.get("DOCMILL_ACTIVE_FRS"):
        inferred = infer_active_frs()
        if inferred is not None:
            env["DOCMILL_ACTIVE_FRS"] = inferred
    if not env.get("DOCMILL_ACTIVE_FRS"):
        print("참고: DOCMILL_ACTIVE_FRS가 비어 있어 인수 테스트는 건너뜁니다.", file=sys.stderr)

    failed: list[str] = []
    for name, cmd in CHECKS:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
        )
        if result.returncode != 0:
            failed.append(name)
            tail = (result.stdout + result.stderr).strip().splitlines()[-30:]
            print(f"[{name}] 실패\n" + "\n".join(tail), file=sys.stderr)
    if failed and retry:
        print(
            f"\n품질 검사가 다시 실패했습니다: {', '.join(failed)}. "
            "반복을 막기 위해 여기서 멈춥니다. PR의 CI가 같은 검사를 다시 실행합니다.",
            file=sys.stderr,
        )
        return 0
    if failed:
        print(
            f"\n품질 검사 실패: {', '.join(failed)}. 테스트가 아니라 구현을 고친 뒤 다시 끝내세요.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
