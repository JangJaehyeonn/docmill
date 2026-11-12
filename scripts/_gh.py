"""자동화 스크립트 공용: GitHub CLI(gh) 호출 도우미. 표준 라이브러리만 쓴다."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Any

FR_LABEL = re.compile(r"^FR-\d{2}$")
PHASE_LABEL = re.compile(r"^phase-(\d)$")
DEPENDS_LINE = re.compile(r"^Depends on:\s*(.*)$", re.MULTILINE)


def gh(*args: str) -> str:
    """gh 명령을 실행하고 표준 출력을 돌려준다. 실패하면 CalledProcessError."""
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return result.stdout


def gh_json(*args: str) -> Any:
    out = gh(*args)
    return json.loads(out) if out.strip() else []


def label_names(item: dict[str, Any]) -> set[str]:
    return {label["name"] for label in item.get("labels", [])}


def fr_of(labels: set[str]) -> str | None:
    for name in sorted(labels):
        if FR_LABEL.match(name):
            return name
    return None


def phase_of(labels: set[str]) -> int:
    for name in labels:
        match = PHASE_LABEL.match(name)
        if match:
            return int(match.group(1))
    return 9


def depends_of(body: str) -> list[str]:
    match = DEPENDS_LINE.search(body or "")
    if not match:
        return []
    return re.findall(r"FR-\d{2}", match.group(1))


def closed_frs() -> set[str]:
    """닫힌(완료된) 이슈의 요구사항 ID 목록. gh 실패는 호출자에게 그대로 전달된다."""
    issues = gh_json("issue", "list", "--state", "closed", "--limit", "500", "--json", "labels")
    done: set[str] = set()
    for issue in issues:
        fr = fr_of(label_names(issue))
        if fr:
            done.add(fr)
    return done


def ensure_label(name: str, color: str = "ededed", description: str = "") -> bool:
    """라벨을 만들거나 갱신한다. 실패하면 stderr에 원인을 남기고 False를 돌려준다."""
    result = subprocess.run(
        ["gh", "label", "create", name, "--color", color, "--description", description, "--force"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"라벨 '{name}' 생성 실패: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True
