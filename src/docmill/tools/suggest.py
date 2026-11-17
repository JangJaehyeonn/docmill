"""`suggest_actions` 업무 도구 (FR-30).

파일을 열었을 때 채팅 패널 맨 위에 보여 줄 작업 제안을 최대 3개 고른다. 파일 종류와 본문의
단서만 보는 규칙 기반이며(AI·엔진·시각·난수 없음), 같은 파일이면 같은 제안을 같은 순서로 돌려준다.
"""

from __future__ import annotations

import html
import json
import re
import zipfile
from pathlib import Path
from typing import Any

MAX_SUGGESTIONS = 3
MAX_XML_BYTES = 20 * 1024 * 1024
_TASK_LINE = re.compile(r"할 일\s*[:：]")


def _suggestion(label: str, tool: str, message: str) -> dict[str, str]:
    return {"label": label, "tool": tool, "message": message}


def _docx_text(path: Path) -> str:
    """docx 본문의 문단 텍스트를 줄 단위로 읽는다. 읽지 못하면 빈 문자열."""
    try:
        with zipfile.ZipFile(path) as archive, archive.open("word/document.xml") as member:
            raw = member.read(MAX_XML_BYTES + 1)
        if len(raw) > MAX_XML_BYTES:
            return ""
        xml = raw.decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""
    xml = xml.replace("</w:p>", "\n")
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def _for_docx(path: Path) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if _TASK_LINE.search(_docx_text(path)):
        items.append(
            _suggestion(
                "할 일 표 만들기",
                "doc_to_task_table",
                f"{path} 문서에서 할 일·담당자·마감일을 뽑아 마감일 순 표(xlsx)로 만들어 줘.",
            )
        )
    items.append(
        _suggestion("서식 맞추기", "apply_template", f"{path} 문서에 회사 서식을 적용해 줘.")
    )
    return items


def _for_xlsx(path: Path) -> list[dict[str, str]]:
    return [
        _suggestion(
            "보고서 PPT 만들기",
            "excel_to_report_ppt",
            f"{path} 엑셀로 그래프가 있는 보고서 PPT를 만들어 줘.",
        )
    ]


def _for_pptx(path: Path) -> list[dict[str, str]]:
    return [
        _suggestion("서식 맞추기", "apply_template", f"{path} 발표 자료에 회사 서식을 적용해 줘."),
        _suggestion(
            "숫자 검증하기",
            "verify_numbers",
            f"{path} 발표 자료의 숫자가 원본과 맞는지 검증해 줘.",
        ),
    ]


_RULES = {".docx": _for_docx, ".xlsx": _for_xlsx, ".pptx": _for_pptx}


def build_suggestions(path: Path) -> list[dict[str, str]]:
    """파일 종류와 내용으로 제안을 고른다 (최대 3개, 규칙 순서 그대로)."""
    rule = _RULES.get(path.suffix.lower())
    return rule(path)[:MAX_SUGGESTIONS] if rule else []


def suggest_actions(input: str) -> str:
    """열린 문서로 할 수 있는 작업을 최대 3개 제안한다 (규칙 기반, AI 없음).

    input은 xlsx·pptx·docx 파일 경로이다. 결과 JSON의 suggestions에 label(버튼 문구),
    tool(docmill 도구 이름), message(채팅으로 보낼 문구)가 담긴다. 같은 파일이면 같은 제안을
    같은 순서로 돌려주고, 지원하지 않는 형식은 빈 목록이다. 실패하면 error를 담는다.
    """
    path = Path(input)
    result: dict[str, Any]
    if not path.is_file():
        result = {"error": f"파일을 찾을 수 없습니다: {input}", "step": "open"}
    else:
        try:
            result = {"suggestions": build_suggestions(path)}
        except Exception as exc:  # corrupt archives raise zlib.error, RuntimeError, ...
            result = {"error": f"제안을 만들지 못했습니다: {exc}", "step": "suggest"}
    return json.dumps(result, ensure_ascii=False)
