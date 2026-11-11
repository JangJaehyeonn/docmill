"""main과 충돌하는 Claude PR 하나를 찾는다.

출력 (GITHUB_OUTPUT 형식): pr=<번호>, branch=<브랜치> (없으면 빈 값)
GitHub는 충돌 여부를 늦게 계산하므로 UNKNOWN이면 잠시 기다렸다 한 번 더 확인한다.
"""

from __future__ import annotations

import sys
import time

from _gh import gh_json, label_names


def query() -> list[dict[str, object]]:
    prs = gh_json(
        "pr",
        "list",
        "--state",
        "open",
        "--json",
        "number,headRefName,mergeable,labels",
    )
    return [
        pr
        for pr in prs
        if str(pr["headRefName"]).startswith("claude/") and "needs-human" not in label_names(pr)
    ]


def main() -> int:
    prs = query()
    if any(pr["mergeable"] == "UNKNOWN" for pr in prs):
        time.sleep(20)
        prs = query()
    for pr in prs:
        if pr["mergeable"] == "CONFLICTING":
            print(f"pr={pr['number']}")
            print(f"branch={pr['headRefName']}")
            return 0
    print("pr=")
    print("branch=")
    return 0


if __name__ == "__main__":
    sys.exit(main())
