"""헤드리스 엔진 MCP 서버를 내부 MCP 클라이언트로 호출하는 공통 어댑터 (FR-02).

엔진 프로세스는 첫 호출 때 작업 폴더에서 띄우고, 어댑터를 닫을 때 끝낸다.
실패(실행 파일 없음, 시간 초과, 엔진 오류)는 예외가 아니라 `EngineResult`로 돌려준다.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import shutil
import time
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar

import anyio
import anyio.from_thread
import mcp.types as mcp_types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from docmill.engines import DEFAULT_TIMEOUT_SECONDS
from docmill.engines.base import EngineResult
from docmill.engines.paths import PathOutsideWorkdirError, resolve_inside

logger = logging.getLogger(__name__)


_PATH_KEY_HINTS = ("path", "file", "dir", "out", "dest", "target", "export", "save")


def _path_like_values(value: Any, key: str = "") -> list[str]:
    """Collect string values whose key looks like a path (sorted traversal, deterministic)."""
    if isinstance(value, dict):
        found: list[str] = []
        for k in sorted(value, key=str):
            found += _path_like_values(value[k], str(k))
        return found
    if isinstance(value, list | tuple):
        return [v for item in value for v in _path_like_values(item, key)]
    if isinstance(value, str) and any(hint in key.lower() for hint in _PATH_KEY_HINTS):
        return [value]
    return []


class EngineUnavailableError(RuntimeError):
    """엔진 실행 파일을 찾지 못했을 때 발생한다."""


class McpEngineTool:
    """엔진 하나를 감싸는 `DocumentTool` 구현. 엔진별 차이는 클래스 속성으로만 둔다."""

    name: ClassVar[str]
    extensions: ClassVar[tuple[str, ...]]
    env_var: ClassVar[str]
    binary: ClassVar[str]
    confine_with_roots: ClassVar[bool] = False
    open_tool: ClassVar[str]
    save_tool: ClassVar[str]
    execute_tool: ClassVar[str]

    def __init__(self, workdir: Path | None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        if workdir is None:
            raise ValueError(f"{self.name}: 작업 폴더 없이는 엔진을 실행하지 않는다")
        self.workdir = workdir.resolve()
        if not self.workdir.is_dir():
            raise ValueError(f"{self.name}: 작업 폴더가 없다: {self.workdir}")
        self.timeout = timeout
        self._stack: contextlib.ExitStack | None = None
        self._portal: anyio.from_thread.BlockingPortal | None = None
        self._session: ClientSession | None = None
        self.owns_workdir = False  # True면 close()가 작업 폴더까지 지운다

    # -- 생명주기 -----------------------------------------------------------
    def __enter__(self) -> McpEngineTool:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """엔진 프로세스를 끝낸다. 여러 번 불러도 안전하다."""
        stack, self._stack = self._stack, None
        self._portal = None
        self._session = None
        if stack is not None:
            try:
                stack.close()
            except Exception as exc:
                logger.warning("%s 엔진 종료 중 오류: %r", self.name, exc)
        if self.owns_workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)

    # -- 실행 파일·인자 -------------------------------------------------------
    def _executable(self) -> str:
        configured = os.environ.get(self.env_var)
        found = shutil.which(configured) if configured else shutil.which(self.binary)
        if found is None:
            hint = f"{self.env_var}={configured}" if configured else f"PATH의 {self.binary}"
            raise EngineUnavailableError(
                f"{self.name} 엔진 실행 파일을 찾을 수 없다 ({hint}). "
                f"{self.env_var} 환경 변수로 경로를 지정한다."
            )
        return found

    def _args(self) -> list[str]:
        args = ["mcp"]
        if self.confine_with_roots:
            root = str(self.workdir)
            args += ["--automation-read-root", root, "--automation-write-root", root]
        return args

    # -- 세션 -----------------------------------------------------------------
    async def _bounded(self, awaitable: Any) -> Any:
        with anyio.fail_after(self.timeout):
            return await awaitable

    def _ensure_session(self) -> ClientSession:
        if self._session is not None and self._portal is not None:
            return self._session
        params = StdioServerParameters(
            command=self._executable(), args=self._args(), cwd=self.workdir
        )
        stack = contextlib.ExitStack()
        try:
            portal = stack.enter_context(anyio.from_thread.start_blocking_portal())
            read, write = stack.enter_context(
                portal.wrap_async_context_manager(stdio_client(params))
            )
            session = stack.enter_context(
                portal.wrap_async_context_manager(ClientSession(read, write))
            )
            portal.call(self._bounded, session.initialize())
        except BaseException:
            self._stack = stack
            self.close()
            raise
        self._stack, self._portal, self._session = stack, portal, session
        return session

    def _call(self, tool: str, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
        session = self._ensure_session()
        assert self._portal is not None
        result = self._portal.call(self._bounded, session.call_tool(tool, arguments))
        assert isinstance(result, mcp_types.CallToolResult)
        return result

    def list_tools(self) -> list[str]:
        """엔진이 제공하는 도구 이름을 정렬해 돌려준다. 실패하면 예외."""
        session = self._ensure_session()
        assert self._portal is not None
        listed = self._portal.call(self._bounded, session.list_tools())
        return sorted(tool.name for tool in listed.tools)

    # -- 결과 변환 -------------------------------------------------------------
    def _run(self, tool: str, arguments: dict[str, Any]) -> EngineResult:
        """엔진 도구 하나를 호출하고 모든 실패를 `EngineResult`로 바꾼다."""
        started = time.monotonic()
        try:
            result = self._call(tool, arguments)
        except EngineUnavailableError as exc:
            return EngineResult(ok=False, message=str(exc), details={"step": "locate"})
        except TimeoutError:
            self.close()  # 멈춘 엔진은 반드시 끝낸다
            message = f"{self.name} 엔진이 제한 시간({self.timeout:g}초, timeout)을 넘겨 종료했다"
            return EngineResult(ok=False, message=message, details={"step": tool})
        except Exception as exc:
            self.close()
            message = f"{self.name} 엔진 호출 실패 ({tool}): {exc!r}"
            return EngineResult(ok=False, message=message, details={"step": tool})
        texts = [c.text for c in result.content if isinstance(c, mcp_types.TextContent)]
        text = "\n".join(texts)
        elapsed = int((time.monotonic() - started) * 1000)
        if result.is_error:
            message = f"{self.name} 엔진 오류 ({tool}): {text}"
            return EngineResult(
                ok=False, message=message, duration_ms=elapsed, details={"step": tool}
            )
        output: Any = result.structured_content if result.structured_content is not None else text
        details: dict[str, Any] = {"step": tool}
        images = [c for c in result.content if isinstance(c, mcp_types.ImageContent)]
        try:
            if images:
                details["images"] = [base64.b64decode(img.data, validate=True) for img in images]
        except ValueError as exc:  # binascii.Error is a ValueError
            message = f"{self.name} 엔진이 잘못된 이미지 데이터를 돌려주었다 ({tool}): {exc}"
            return EngineResult(
                ok=False, message=message, duration_ms=elapsed, details={"step": tool}
            )
        return EngineResult(ok=True, output=output, duration_ms=elapsed, details=details)

    def _check(self, *paths: Path | str) -> list[Path] | EngineResult:
        try:
            return [resolve_inside(self.workdir, p) for p in paths]
        except PathOutsideWorkdirError as exc:
            return EngineResult(ok=False, message=str(exc), details={"step": "path-check"})
        except (OSError, RuntimeError, ValueError) as exc:  # symlink loop, NUL byte
            message = f"경로를 해석하지 못했다: {exc}"
            return EngineResult(ok=False, message=message, details={"step": "path-check"})

    # -- DocumentTool -----------------------------------------------------------
    def open(self, path: Path) -> EngineResult:
        """작업 폴더 안의 문서를 연다."""
        checked = self._check(path)
        if isinstance(checked, EngineResult):
            return checked
        return self._run(self.open_tool, {"path": str(checked[0])})

    def save(self, path: Path) -> EngineResult:
        """현재 문서를 작업 폴더 안의 경로에 저장한다."""
        checked = self._check(path)
        if isinstance(checked, EngineResult):
            return checked
        return self._run(self.save_tool, {"path": str(checked[0])})

    def execute(self, command: str, args: dict[str, Any]) -> EngineResult:
        """엔진 명령 하나를 실행한다. 경로로 보이는 인자는 호출 전에 검사한다."""
        checked = self._check(*_path_like_values(args))
        if isinstance(checked, EngineResult):
            return checked
        return self._run(self.execute_tool, {"command": command, "params": args})

    def render(self, path: Path, out_dir: Path) -> EngineResult:
        """문서를 열어 PNG로 만들어 `out_dir`에 둔다. 두 경로 모두 작업 폴더 안이어야 한다."""
        checked = self._check(path, out_dir)
        if isinstance(checked, EngineResult):
            return checked
        source, target_dir = checked
        opened = self._run(self.open_tool, {"path": str(source)})
        if not opened.ok:
            return opened
        return self._render_opened(source, target_dir)

    def _render_opened(self, source: Path, out_dir: Path) -> EngineResult:
        raise NotImplementedError
