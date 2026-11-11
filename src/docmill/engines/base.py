"""모든 문서 엔진이 따르는 공통 인터페이스 (FR-03).

새 엔진은 이 Protocol을 구현하는 클래스 하나만 추가하면 된다 (NFR-09).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class EngineResult:
    """엔진 명령 한 번의 결과."""

    ok: bool
    output: Any = None
    message: str = ""
    duration_ms: int = 0
    details: dict[str, Any] = field(default_factory=dict)


class DocumentTool(Protocol):
    """문서 엔진 어댑터 공통 규약.

    - 모든 파일 작업은 작업별 임시 폴더 안에서만 한다 (NFR-05).
    - 같은 입력이면 같은 결과를 내야 한다 (NFR-01).
    """

    name: str
    extensions: tuple[str, ...]

    def open(self, path: Path) -> EngineResult:
        """문서를 연다."""
        ...

    def execute(self, command: str, args: dict[str, Any]) -> EngineResult:
        """엔진 명령 하나를 실행한다."""
        ...

    def save(self, path: Path) -> EngineResult:
        """현재 문서를 저장한다."""
        ...

    def render(self, path: Path, out_dir: Path) -> EngineResult:
        """문서를 미리보기 이미지(PNG)로 만든다."""
        ...
