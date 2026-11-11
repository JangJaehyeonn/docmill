"""자동 수정 횟수 관리. 같은 PR에 3번까지만 자동 수정을 허용한다.

사용: python3 scripts/fix_attempt.py <PR 번호> <ci|coderabbit>
출력 (GITHUB_OUTPUT 형식): proceed=true|false, n=<이번 시도 번호>
"""

from __future__ import annotations

import re
import sys

from _gh import ensure_label, gh, gh_json, label_names

MAX_ATTEMPTS = 3
OWNER = "@JangJaehyeonn"


def main() -> int:
    pr, kind = sys.argv[1], sys.argv[2]
    labels = label_names(gh_json("pr", "view", pr, "--json", "labels"))
    if "needs-human" in labels:
        print("proceed=false")
        print("n=0")
        return 0

    used = [int(m.group(1)) for name in labels if (m := re.match(r"^fix-attempt-(\d)$", name))]
    n = max(used, default=0) + 1
    if n > MAX_ATTEMPTS:
        ensure_label("needs-human", "b60205", "에이전트가 해결하지 못해 사람 확인이 필요함")
        gh("pr", "edit", pr, "--add-label", "needs-human")
        gh(
            "pr",
            "comment",
            pr,
            "--body",
            f"{OWNER} 자동 수정({kind})을 {MAX_ATTEMPTS}번 시도했지만 해결되지 않았습니다. "
            "확인이 필요해 자동화를 멈춥니다. 원인을 정리한 뒤 `needs-human` 라벨을 지우면 다시 진행합니다.",
        )
        print("proceed=false")
        print(f"n={n}")
        return 0

    label = f"fix-attempt-{n}"
    ensure_label(label, "fbca04", f"자동 수정 {n}번째 시도")
    gh("pr", "edit", pr, "--add-label", label)
    print("proceed=true")
    print(f"n={n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
