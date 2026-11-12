"""CI용: 인수 테스트를 실행할 요구사항 목록을 계산해 `DOCMILL_ACTIVE_FRS=...` 한 줄로 출력한다.

대상 = 이미 완료된(닫힌 이슈) 요구사항 + 지금 PR 제목에 있는 요구사항.
GitHub Actions에서 완료 목록 조회에 실패하면 0이 아닌 코드로 끝나 CI를 실패시킨다.
(조용히 빈 목록으로 넘어가면 이미 끝난 요구사항의 회귀를 놓치기 때문이다.)

사용: python3 scripts/active_frs.py "<PR 제목>" >> "$GITHUB_ENV"
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

from _gh import closed_frs


def main() -> int:
    title = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        active = closed_frs()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"완료된 요구사항 목록을 가져오지 못했습니다: {exc}", file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            return 1
        active = set()
    active.update(fr.upper() for fr in re.findall(r"FR-\d{2}", title, flags=re.IGNORECASE))
    print("DOCMILL_ACTIVE_FRS=" + ",".join(sorted(active)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
