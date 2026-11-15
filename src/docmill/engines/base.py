"""모든 문서 엔진이 따르는 공통 인터페이스 (FR-03).

새 엔진은 이 Protocol을 구현하는 클래스 하나만 추가하면 된다 (NFR-09).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


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


@runtime_checkable
class DirectToolCaller(Protocol):
    """엔진이 직접 제공하는 MCP 도구를 이름으로 호출하는 확장 규약.

    `DocumentTool`의 명령 ID 경로(`execute`)로 표현할 수 없는 도구 호출에 쓴다.
    구현체는 경로로 보이는 인자를 호출 전에 작업 폴더 안인지 검사해야 한다.
    """

    def call_tool(self, tool: str, arguments: dict[str, Any]) -> EngineResult:
        """엔진 MCP 도구 하나를 호출한다. 실패는 `EngineResult`로 돌려준다."""
        ...
