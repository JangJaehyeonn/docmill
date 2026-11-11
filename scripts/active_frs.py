"""CI용: 인수 테스트를 실행할 요구사항 목록을 계산해 `DOCMILL_ACTIVE_FRS=...` 한 줄로 출력한다.

대상 = 이미 완료된(닫힌 이슈) 요구사항 + 지금 PR 제목에 있는 요구사항.
사용: python3 scripts/active_frs.py "<PR 제목>" >> "$GITHUB_ENV"
"""

from __future__ import annotations

import re
import subprocess
import sys

from _gh import closed_frs


def main() -> int:
    title = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        active = closed_frs()
    except (subprocess.CalledProcessError, FileNotFoundError):
        active = set()
    active.update(fr.upper() for fr in re.findall(r"FR-\d{2}", title, flags=re.IGNORECASE))
    print("DOCMILL_ACTIVE_FRS=" + ",".join(sorted(active)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
