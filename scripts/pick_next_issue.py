"""이슈 공급기: Claude가 다음에 작업할 이슈 하나를 고른다.

출력 (GITHUB_OUTPUT 형식):
  issue=<번호 또는 빈 값>
  fr=<요구사항 ID 또는 빈 값>

규칙
- Claude 브랜치(claude/*)의 PR이 열려 있으면 고르지 않는다 (한 번에 하나).
- `ready` 라벨이 있고, `needs-human`·`in-progress`가 없는 열린 이슈만 후보.
- 본문의 `Depends on:` 요구사항이 모두 완료(닫힘)된 이슈만 후보.
- Phase가 낮은 순, 이슈 번호가 작은 순.
"""

from __future__ import annotations

import sys

from _gh import closed_frs, depends_of, fr_of, gh_json, label_names, phase_of

BLOCKING = {"needs-human", "in-progress"}
# 복잡한 구현은 이슈에 `opus` 라벨을 붙여 Opus로, 나머지는 Sonnet으로 한다.
OPUS_LABEL = "opus"
OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"


def emit(issue: str = "", fr: str = "", model: str = SONNET) -> None:
    print(f"issue={issue}")
    print(f"fr={fr}")
    print(f"model={model}")


def main() -> int:
    open_prs = gh_json("pr", "list", "--state", "open", "--json", "headRefName")
    if any(pr["headRefName"].startswith("claude/") for pr in open_prs):
        print("Claude PR이 아직 열려 있어 이번에는 건너뜁니다.", file=sys.stderr)
        emit()
        return 0

    issues = gh_json(
        "issue",
        "list",
        "--state",
        "open",
        "--label",
        "ready",
        "--limit",
        "200",
        "--json",
        "number,labels,body",
    )
    done = closed_frs()
    candidates: list[tuple[int, int, str, str]] = []
    for issue in issues:
        labels = label_names(issue)
        if labels & BLOCKING:
            continue
        fr = fr_of(labels)
        if not fr:
            continue
        if not set(depends_of(issue.get("body", ""))) <= done:
            continue
        model = OPUS if OPUS_LABEL in labels else SONNET
        candidates.append((phase_of(labels), int(issue["number"]), fr, model))

    if not candidates:
        print("작업할 수 있는 이슈가 없습니다.", file=sys.stderr)
        emit()
        return 0

    _, number, fr, model = sorted(candidates)[0]
    print(f"다음 작업: #{number} ({fr}, {model})", file=sys.stderr)
    emit(str(number), fr, model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
