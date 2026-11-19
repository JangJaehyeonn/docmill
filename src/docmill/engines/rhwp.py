"""rhwp(`@rhwp/core`, Node) 한글 문서 읽기 어댑터 (FR-35, ADR 0025).

`DocumentTool` 규약을 따른다. 읽기 전용이라 `execute("get_text")`(줄 목록)·
`execute("get_paragraphs")`(문단별 스타일·글자)·`execute("get_tables")`(표 셀)만 지원하고,
실패는 예외 대신 `EngineResult`로 돌려준다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from docmill.engines.base import EngineResult

_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "hwp_text.mjs"


def _core_dir() -> Path | None:
    """@rhwp/core 폴더를 찾는다. DOCMILL_RHWP_CORE가 있으면 그것을, 없으면 저장소 studio/를 쓴다."""
    override = os.environ.get("DOCMILL_RHWP_CORE")
    candidates = [Path(override)] if override else []
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "studio" / "node_modules" / "@rhwp" / "core")
    for candidate in candidates:
        if (candidate / "rhwp.js").is_file():
            # Node runs inside the job temp folder, so a relative override must be absolute.
            return candidate.resolve()
    return None


class RhwpTool:
    """hwp·hwpx 문단을 읽는 `DocumentTool` 어댑터. 원본은 작업 폴더로 복사해서 읽는다."""

    name = "rhwp"
    extensions = (".hwp", ".hwpx")

    def __init__(self, workdir: Path, timeout: float = 60) -> None:
        self.workdir = workdir
        self.timeout = timeout
        self._staged: Path | None = None

    def open(self, path: Path) -> EngineResult:
        """원본을 작업 폴더로 복사한다."""
        try:
            staged = self.workdir / f"input{path.suffix.lower()}"
            shutil.copyfile(path, staged)
        except OSError as exc:
            return EngineResult(ok=False, message=f"한글 문서를 열지 못했다: {exc}")
        self._staged = staged
        return EngineResult(ok=True)

    def execute(self, command: str, args: dict[str, Any]) -> EngineResult:
        """`get_text`는 줄 목록(list[str]), `get_paragraphs`는 문단 목록(list[dict])을 돌려준다."""
        modes = {"get_text": "lines", "get_paragraphs": "paragraphs", "get_tables": "tables"}
        if command not in modes:
            return EngineResult(ok=False, message=f"지원하지 않는 명령: {command}")
        if self._staged is None:
            return EngineResult(ok=False, message="문서가 열려 있지 않다")
        return self._read(self._staged, modes[command])

    def save(self, path: Path) -> EngineResult:
        """읽기 전용이라 저장하지 않는다."""
        return EngineResult(ok=False, message="한글 문서는 읽기만 지원한다")

    def render(self, path: Path, out_dir: Path) -> EngineResult:
        """미리보기를 지원하지 않는다."""
        return EngineResult(ok=False, message="한글 문서 미리보기는 지원하지 않는다")

    def _read(self, staged: Path, mode: str = "lines") -> EngineResult:
        node = shutil.which("node")
        core = _core_dir()
        if node is None:
            return EngineResult(ok=False, message="한글 문서를 읽으려면 Node가 필요하다")
        if core is None:
            return EngineResult(
                ok=False,
                message="한글 문서 읽기 도구(@rhwp/core)를 찾지 못했다: studio 의존성 설치 필요",
            )
        started = time.monotonic()
        try:
            done = subprocess.run(
                [node, str(_SCRIPT), str(core), str(staged), mode],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout,
                check=False,
                cwd=self.workdir,
            )
        except subprocess.TimeoutExpired:
            return EngineResult(ok=False, message=f"한글 문서 읽기가 {self.timeout:g}초를 넘겼다")
        except OSError as exc:
            return EngineResult(ok=False, message=f"한글 문서 읽기를 시작하지 못했다: {exc}")
        millis = int((time.monotonic() - started) * 1000)
        if done.returncode != 0:
            reason = done.stderr.strip() or f"종료 코드 {done.returncode}"
            return EngineResult(ok=False, message=f"한글 문서를 읽지 못했다: {reason}")
        try:
            data = json.loads(done.stdout)
        except json.JSONDecodeError:
            return EngineResult(ok=False, message="한글 문서 읽기 결과를 해석하지 못했다")
        if not isinstance(data, list):
            return EngineResult(ok=False, message="한글 문서 읽기 결과를 해석하지 못했다")
        if mode in ("paragraphs", "tables"):
            if not all(isinstance(x, dict) for x in data):
                return EngineResult(ok=False, message="한글 문서 읽기 결과를 해석하지 못했다")
            return EngineResult(ok=True, output=[dict(x) for x in data], duration_ms=millis)
        return EngineResult(ok=True, output=[str(x) for x in data], duration_ms=millis)


def _run(path: Path, command: str, timeout: float) -> EngineResult:
    with tempfile.TemporaryDirectory(prefix="docmill-hwp-") as tmp:
        tool = RhwpTool(Path(tmp).resolve(), timeout)
        opened = tool.open(path)
        return opened if not opened.ok else tool.execute(command, {})


def read_lines(path: Path, timeout: float = 60) -> EngineResult:
    """임시 작업 폴더에서 hwp·hwpx 문단 글자를 줄 목록으로 읽는다."""
    return _run(path, "get_text", timeout)


def read_tables(path: Path, timeout: float = 60) -> EngineResult:
    """임시 작업 폴더에서 hwp·hwpx 본문 표의 셀을 구역·문단·표·행·열 번호(1부터)와 함께 읽는다."""
    return _run(path, "get_tables", timeout)


def read_paragraphs(path: Path, timeout: float = 60) -> EngineResult:
    """임시 작업 폴더에서 hwp·hwpx 문단을 스타일 이름과 함께 읽는다 (구역·문단 번호는 1부터)."""
    return _run(path, "get_paragraphs", timeout)
