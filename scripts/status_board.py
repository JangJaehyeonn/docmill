"""진행 현황판: 요구사항·이슈·PR 상태를 모아 고정 이슈 하나의 본문으로 갱신한다.

사용:
  python3 scripts/status_board.py            # 현황판 이슈를 만들거나 갱신
  python3 scripts/status_board.py --print    # 본문만 출력 (갱신 안 함)

GitHub REST API만 쓴다 (gh api). 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from _gh import FR_LABEL, gh, label_names

BOARD_TITLE = "📊 docmill 진행 현황"
BOARD_LABEL = "status-board"
KST = timedelta(hours=9)
PHASE_NAMES = {1: "기반", 2: "업무 도구", 3: "레시피", 4: "배포·운영"}
ROOT = Path(__file__).resolve().parent.parent


def gh_input(args: list[str], text: str) -> str:
    """표준 입력으로 JSON을 넘겨 gh를 실행한다."""
    result = subprocess.run(["gh", *args], input=text, capture_output=True, text=True, check=True)
    return result.stdout


def repo() -> str:
    """현재 저장소 이름(owner/name)."""
    env = os.environ.get("GITHUB_REPOSITORY")
    if env:
        return env
    return str(gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")).strip()


def api_list(path: str) -> list[dict[str, Any]]:
    """페이지를 넘기며 REST 목록을 모두 가져온다."""
    out = gh("api", "--paginate", path, "--jq", ".[] | @json")
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def kst(ts: str | None) -> str:
    if not ts:
        return ""
    when = datetime.fromisoformat(ts.replace("Z", "+00:00")) + KST
    return when.strftime("%m/%d %H:%M")


def bar(done: int, total: int, width: int = 10) -> str:
    filled = round(width * done / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def load_requirements() -> list[dict[str, Any]]:
    with (ROOT / "docs/requirements/issues.toml").open("rb") as f:
        return list(tomllib.load(f)["requirement"])


def build(name: str) -> str:
    """현황판 본문(마크다운)을 만든다."""
    reqs = load_requirements()
    issues = [
        i
        for i in api_list(f"repos/{name}/issues?state=all&per_page=100")
        if "pull_request" not in i
    ]
    pulls = api_list(f"repos/{name}/pulls?state=all&per_page=100")

    by_fr: dict[str, dict[str, Any]] = {}
    for issue in issues:
        frs = [n for n in label_names(issue) if FR_LABEL.match(n)]
        if frs:
            by_fr.setdefault(frs[0], issue)

    def pr_for(number: int) -> dict[str, Any] | None:
        prefix = f"claude/{number}-"
        cands = [p for p in pulls if p["head"]["ref"].startswith(prefix)]
        merged = [p for p in cands if p.get("merged_at")]
        opened = [p for p in cands if p["state"] == "open"]
        return (merged or opened or cands or [None])[0]

    rows: dict[int, list[str]] = {}
    counts: dict[int, list[int]] = {}
    done_log: list[tuple[str, str]] = []
    working: list[str] = []
    attention: list[str] = []
    next_up: list[str] = []

    for req in reqs:
        fr, phase = req["id"], int(req["phase"])
        counts.setdefault(phase, [0, 0])
        counts[phase][1] += 1
        issue = by_fr.get(fr)
        labels = label_names(issue) if issue else set()
        pr = pr_for(issue["number"]) if issue else None
        pr_link = f"[#{pr['number']}]({pr['html_url']})" if pr else ""
        issue_link = f"[#{issue['number']}]({issue['html_url']})" if issue else "-"

        if issue and issue["state"] == "closed":
            state = "✅ 완료"
            counts[phase][0] += 1
            when = (pr or {}).get("merged_at") or issue.get("closed_at")
            done_log.append((when or "", f"{fr} {req['title']} {pr_link}"))
        elif "needs-human" in labels or (pr and "needs-human" in label_names(pr)):
            state = "🛑 확인 필요"
            attention.append(f"{fr} {req['title']} {issue_link} {pr_link}".strip())
        elif pr and pr["state"] == "open":
            state = "🔄 리뷰·수정 중"
            working.append(f"{fr} {req['title']} {pr_link}")
        elif "in-progress" in labels:
            state = "🔨 구현 중"
            working.append(f"{fr} {req['title']} {issue_link}")
        elif "ready" in labels:
            state = "⏳ 대기"
            next_up.append(f"{fr} {req['title']}")
        elif issue:
            state = "📝 인수 테스트 대기"
        else:
            state = "· 예정"
        rows.setdefault(phase, []).append(
            f"| {fr} | {req['title']} | {state} | {issue_link} | {pr_link} |"
        )

    human_prs = [
        p
        for p in pulls
        if p["state"] == "open"
        and not p["head"]["ref"].startswith(("claude/", "dependabot/", "release-please--"))
    ]
    attention += [
        f"PR 머지 필요: [#{p['number']}]({p['html_url']}) {p['title']}" for p in human_prs
    ]

    total_done = sum(c[0] for c in counts.values())
    total = sum(c[1] for c in counts.values())
    now = (datetime.now(UTC) + KST).strftime("%Y-%m-%d %H:%M")
    out = [
        f"_자동 갱신: {now} KST · 이 이슈는 워크플로가 고쳐 씁니다._",
        "",
        f"## 전체 {total_done}/{total}  `{bar(total_done, total, 20)}`",
        "",
        "| Phase | 진행 | |",
        "| --- | --- | --- |",
    ]
    for phase in sorted(counts):
        d, t = counts[phase]
        out.append(f"| {phase}. {PHASE_NAMES.get(phase, '')} | `{bar(d, t)}` | {d}/{t} |")

    out += ["", "## 🙋 재현이 할 일"]
    out += [f"- {a}" for a in attention] or ["- 없음"]
    out += ["", "## 🔨 진행 중"]
    out += [f"- {w}" for w in working] or ["- 없음"]
    out += ["", "## ⏭️ 다음 순서"]
    out += [f"- {n}" for n in next_up[:5]] or [
        "- `ready` 이슈 없음 (인수 테스트 승인 대기일 수 있음)"
    ]
    out += ["", "## ✅ 최근 완료"]
    recent = sorted(done_log, reverse=True)[:8]
    out += [f"- {kst(ts)} · {text}" for ts, text in recent] or ["- 아직 없음"]
    out += ["", "<details><summary>요구사항 전체</summary>", ""]
    for phase in sorted(rows):
        out += [
            f"### Phase {phase}. {PHASE_NAMES.get(phase, '')}",
            "",
            "| ID | 요구사항 | 상태 | 이슈 | PR |",
            "| --- | --- | --- | --- | --- |",
            *rows[phase],
            "",
        ]
    out.append("</details>")
    return "\n".join(out) + "\n"


def upsert(name: str, body: str) -> None:
    """현황판 이슈가 있으면 본문을 고치고, 없으면 만든다."""
    found = api_list(f"repos/{name}/issues?state=open&labels={BOARD_LABEL}&per_page=10")
    payload = json.dumps({"title": BOARD_TITLE, "body": body})
    if found:
        number = found[0]["number"]
        gh_input(["api", "-X", "PATCH", f"repos/{name}/issues/{number}", "--input", "-"], payload)
        print(f"현황판 갱신: #{number}")
        return
    gh("label", "create", BOARD_LABEL, "--repo", name, "--color", "0e8a16", "--force")
    new = json.dumps({"title": BOARD_TITLE, "body": body, "labels": [BOARD_LABEL]})
    created = json.loads(
        gh_input(["api", "-X", "POST", f"repos/{name}/issues", "--input", "-"], new)
    )
    print(f"현황판 생성: #{created['number']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print", action="store_true", help="본문만 출력")
    parser.add_argument(
        "--notion", action="store_true", help="노션 페이지에도 반영 (NOTION_TOKEN, NOTION_PAGE_ID)"
    )
    args = parser.parse_args()
    name = repo()
    body = build(name)
    if args.print:
        sys.stdout.write(body)
        return 0
    upsert(name, body)
    if args.notion:
        from notion_sync import sync

        sync(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
