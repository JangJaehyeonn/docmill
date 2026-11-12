"""docs/requirements/issues.toml의 요구사항을 GitHub 이슈로 만든다.

사용 예
  python scripts/create_issues.py --phase 1            # Phase 1 이슈 생성 (아직 ready 아님)
  python scripts/create_issues.py --phase 1 --ready    # 생성하면서 ready 라벨까지 (Phase 시작)
  python scripts/create_issues.py --mark-ready 2       # 이미 만든 Phase 2 이슈를 ready로
  python scripts/create_issues.py --phase 1 --dry-run  # 만들 내용만 출력

같은 요구사항 ID 라벨을 가진 이슈가 이미 있으면 다시 만들지 않는다.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any

from _gh import fr_of, gh, gh_json, label_names


def load() -> list[dict[str, Any]]:
    spec = tomllib.loads(Path("docs/requirements/issues.toml").read_text(encoding="utf-8"))
    return list(spec["requirement"])


def body_of(req: dict[str, Any]) -> str:
    criteria = "\n".join(f"- [ ] {c}" for c in req["acceptance"])
    depends = ", ".join(req.get("depends", [])) or "없음"
    return (
        f"## 요구사항\n**{req['id']}** · {req['area']} · {req['priority']} · Phase {req['phase']}\n\n"
        f"{req['detail']}\n\n"
        f"## 인수 기준\n{criteria}\n\n"
        f"## 의존\nDepends on: {depends}\n\n"
        "---\n이 이슈는 `docs/requirements/issues.toml`에서 생성되었습니다. "
        "구현 규칙은 `CLAUDE.md`를 따릅니다.\n"
    )


def existing() -> dict[str, dict[str, Any]]:
    issues = gh_json("issue", "list", "--state", "all", "--limit", "500", "--json", "number,labels")
    found: dict[str, dict[str, Any]] = {}
    for issue in issues:
        fr = fr_of(label_names(issue))
        if fr:
            found[fr] = issue
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=int)
    parser.add_argument("--ready", action="store_true")
    parser.add_argument("--mark-ready", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    reqs = load()
    have = {} if args.dry_run else existing()

    if args.mark_ready is not None:
        for req in reqs:
            if req["phase"] == args.mark_ready and req["id"] in have:
                gh("issue", "edit", str(have[req["id"]]["number"]), "--add-label", "ready")
                print(f"ready: {req['id']}")
        return 0

    if args.phase is None:
        parser.error("--phase 또는 --mark-ready 중 하나가 필요합니다.")

    for req in reqs:
        if req["phase"] != args.phase:
            continue
        if req["id"] in have:
            print(f"건너뜀 (이미 있음): {req['id']}")
            continue
        labels = [req["id"], f"phase-{req['phase']}", req["priority"]]
        if args.ready:
            labels.append("ready")
        title = f"{req['id']} {req['title']}"
        if args.dry_run:
            print(f"--- {title}  [{', '.join(labels)}]\n{body_of(req)}")
            continue
        url = gh(
            "issue",
            "create",
            "--title",
            title,
            "--body",
            body_of(req),
            "--label",
            ",".join(labels),
        ).strip()
        print(f"생성: {title} -> {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
