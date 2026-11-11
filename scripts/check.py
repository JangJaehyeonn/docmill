"""Claude Code Stop 훅: 작업을 끝내기 전에 품질 검사를 실행한다.

검사가 하나라도 실패하면 종료 코드 2를 돌려준다.
Claude Code는 종료 코드 2를 받으면 작업을 끝내지 않고 stderr 내용을 보고 계속 고친다.
Windows·macOS·Linux 모두에서 동작하도록 셸 스크립트 대신 Python으로 작성했다.
"""

from __future__ import annotations

import subprocess
import sys

CHECKS: list[tuple[str, list[str]]] = [
    ("ruff check", ["uv", "run", "ruff", "check", "."]),
    ("ruff format", ["uv", "run", "ruff", "format", "--check", "."]),
    ("mypy", ["uv", "run", "mypy"]),
    ("pytest", ["uv", "run", "pytest", "-q", "-x"]),
]


def main() -> int:
    failed: list[str] = []
    for name, cmd in CHECKS:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            failed.append(name)
            tail = (result.stdout + result.stderr).strip().splitlines()[-30:]
            print(f"[{name}] 실패\n" + "\n".join(tail), file=sys.stderr)
    if failed:
        print(
            f"\n품질 검사 실패: {', '.join(failed)}. 테스트가 아니라 구현을 고친 뒤 다시 끝내세요.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
