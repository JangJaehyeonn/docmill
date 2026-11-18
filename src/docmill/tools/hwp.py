"""한글(hwp·hwpx) 문서의 글자를 rhwp 어댑터(`engines/rhwp.py`)로 읽는다.

업무 도구(doc_to_task_table·review_map·preflight)가 docx처럼 쓰도록 문단 목록을 돌려준다.
"""

from __future__ import annotations

from pathlib import Path

from docmill.engines.rhwp import read_lines

HWP_SUFFIXES = (".hwp", ".hwpx")


class HwpError(Exception):
    """한글 문서를 읽지 못했을 때의 이유를 담는다."""


def is_hwp(path: Path) -> bool:
    """확장자가 hwp·hwpx인지 확인한다."""
    return path.suffix.lower() in HWP_SUFFIXES


def read_hwp_lines(path: Path) -> list[str]:
    """hwp·hwpx 파일의 문단(줄) 목록을 문서 순서대로 읽는다. 읽을 수 없으면 HwpError."""
    result = read_lines(path)
    if not result.ok:
        raise HwpError(result.message)
    return list(result.output)
