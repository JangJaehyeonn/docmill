"""진행 현황판을 노션 페이지에도 반영한다 (status_board.py가 만든 마크다운을 노션 블록으로 바꿔 덮어쓴다).

환경 변수:
  NOTION_TOKEN    노션 내부 연동(Internal integration) 비밀값. 대상 페이지에 연결돼 있어야 한다.
  NOTION_PAGE_ID  덮어쓸 노션 페이지 ID
둘 중 하나라도 없으면 아무것도 하지 않고 끝낸다 (선택 기능).

표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from typing import Any

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)|`([^`]+)`")
MAX_CHILDREN = 100


def call(method: str, path: str, token: str, body: dict[str, Any] | None = None) -> Any:
    """노션 API를 호출하고 JSON 응답을 돌려준다."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read() or b"{}")


def rich(text: str) -> list[dict[str, Any]]:
    """[라벨](URL)과 `코드`를 노션 rich text로 바꾼다."""
    parts: list[dict[str, Any]] = []
    pos = 0
    for m in LINK.finditer(text):
        if m.start() > pos:
            parts.append({"type": "text", "text": {"content": text[pos : m.start()]}})
        if m.group(3) is not None:
            parts.append(
                {"type": "text", "text": {"content": m.group(3)}, "annotations": {"code": True}}
            )
        else:
            parts.append(
                {"type": "text", "text": {"content": m.group(1), "link": {"url": m.group(2)}}}
            )
        pos = m.end()
    if pos < len(text):
        parts.append({"type": "text", "text": {"content": text[pos:]}})
    return [p for p in parts if p["text"]["content"]]


def block(kind: str, text: str) -> dict[str, Any]:
    return {"object": "block", "type": kind, kind: {"rich_text": rich(text)}}


def table(lines: list[str]) -> dict[str, Any]:
    """파이프 표(머리줄, 구분줄, 본문)를 노션 표 블록으로 바꾼다."""
    rows = [
        [c.strip() for c in line.strip().strip("|").split("|")]
        for i, line in enumerate(lines)
        if i != 1
    ]
    width = max(len(r) for r in rows)
    children = [
        {
            "object": "block",
            "type": "table_row",
            "table_row": {"cells": [rich(c) for c in r + [""] * (width - len(r))]},
        }
        for r in rows
    ]
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": True,
            "has_row_header": False,
            "children": children,
        },
    }


def to_blocks(markdown: str) -> list[dict[str, Any]]:
    """status_board 마크다운을 노션 블록 목록으로 바꾼다 (쓰는 문법만 지원)."""
    out: list[dict[str, Any]] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("|"):
            j = i
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            out.append(table(lines[i:j]))
            i = j
            continue
        if line.startswith("<details>"):
            out.append(block("heading_2", "요구사항 전체"))
        elif line.startswith("</details>") or not line.strip():
            pass
        elif line.startswith("### "):
            out.append(block("heading_3", line[4:]))
        elif line.startswith("## "):
            out.append(block("heading_2", line[3:]))
        elif line.startswith("- "):
            out.append(block("bulleted_list_item", line[2:]))
        else:
            out.append(block("paragraph", line.strip("_")))
        i += 1
    return out


def replace_page(page_id: str, token: str, blocks: list[dict[str, Any]]) -> None:
    """페이지의 기존 블록을 지우고 새 블록으로 채운다."""
    cursor: str | None = None
    old: list[str] = []
    while True:
        query = f"?page_size=100&start_cursor={cursor}" if cursor else "?page_size=100"
        res = call("GET", f"/blocks/{page_id}/children{query}", token)
        old += [b["id"] for b in res.get("results", [])]
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]
    for block_id in old:
        call("DELETE", f"/blocks/{block_id}", token)
    for start in range(0, len(blocks), MAX_CHILDREN):
        chunk = blocks[start : start + MAX_CHILDREN]
        call("PATCH", f"/blocks/{page_id}/children", token, {"children": chunk})


def sync(markdown: str) -> int:
    """환경 변수가 있으면 노션 페이지를 갱신한다. 실패해도 현황판 갱신은 막지 않는다."""
    token = os.environ.get("NOTION_TOKEN", "").strip()
    page_id = os.environ.get("NOTION_PAGE_ID", "").strip()
    if not token or not page_id:
        print("노션 동기화 건너뜀 (NOTION_TOKEN 또는 NOTION_PAGE_ID 없음)")
        return 0
    try:
        replace_page(page_id, token, to_blocks(markdown))
    except Exception as exc:  # optional mirror must not fail the board
        print(f"::warning::노션 동기화 실패: {exc}", file=sys.stderr)
        return 0
    print("노션 페이지 갱신 완료")
    return 0
