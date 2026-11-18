"""한글(hwp·hwpx) 문서의 글자를 rhwp(`@rhwp/core`, Node)로 읽는다.

업무 도구(doc_to_task_table·review_map·preflight)가 docx처럼 쓰도록 문단 목록을 돌려준다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

HWP_SUFFIXES = (".hwp", ".hwpx")
_TIMEOUT_SECONDS = 60
_SCRIPT = Path(__file__).with_name("hwp_text.mjs")


class HwpError(Exception):
    """한글 문서를 읽지 못했을 때의 이유를 담는다."""


def is_hwp(path: Path) -> bool:
    """확장자가 hwp·hwpx인지 확인한다."""
    return path.suffix.lower() in HWP_SUFFIXES


def _core_dir() -> Path:
    """@rhwp/core 폴더를 찾는다. DOCMILL_RHWP_CORE가 있으면 그것을, 없으면 저장소 studio/를 쓴다."""
    override = os.environ.get("DOCMILL_RHWP_CORE")
    candidates = [Path(override)] if override else []
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "studio" / "node_modules" / "@rhwp" / "core")
    for candidate in candidates:
        if (candidate / "rhwp.js").is_file():
            return candidate
    raise HwpError("한글 문서 읽기 도구(@rhwp/core)를 찾지 못했다: studio 의존성을 설치해야 한다")


def read_hwp_lines(path: Path) -> list[str]:
    """hwp·hwpx 파일의 문단(줄) 목록을 문서 순서대로 읽는다. 읽을 수 없으면 HwpError."""
    node = shutil.which("node")
    if node is None:
        raise HwpError("한글 문서를 읽으려면 Node가 필요하다")
    try:
        done = subprocess.run(
            [node, str(_SCRIPT), str(_core_dir()), str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HwpError(f"한글 문서 읽기가 {_TIMEOUT_SECONDS}초를 넘겼다") from exc
    if done.returncode != 0:
        reason = done.stderr.strip() or f"종료 코드 {done.returncode}"
        raise HwpError(f"한글 문서를 읽지 못했다: {reason}")
    try:
        lines = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise HwpError("한글 문서 읽기 결과를 해석하지 못했다") from exc
    return [str(line) for line in lines]
