"""docmill: 문서 작업을 레시피로 고정해 반복 실행하는 MCP 서버."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("docmill")
except PackageNotFoundError:  # pragma: no cover - 소스 트리에서 직접 실행할 때
    __version__ = "0.1.0"
