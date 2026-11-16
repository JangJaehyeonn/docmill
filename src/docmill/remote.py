"""원격 HTTP 모드 운영 기능: 토큰 인증, 바인딩 주소 제한, 헬스 체크 (FR-24).

- `DOCMILL_HTTP_TOKEN`이 있으면 `/healthz`를 뺀 모든 요청에
  `Authorization: Bearer <토큰>`을 요구한다.
- 토큰이 없으면 `--host`와 상관없이 127.0.0.1에만 바인딩한다.
- `/healthz`는 엔진 3개의 준비 상태를 JSON으로 돌려주고, 하나라도 준비되지 않으면 503이다.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from docmill.engines.deckcraft import DeckCraftTool
from docmill.engines.gridcraft import GridCraftTool
from docmill.engines.mcp_tool import EngineUnavailableError, McpEngineTool
from docmill.engines.wordcraft import WordCraftTool

logger = logging.getLogger(__name__)

TOKEN_ENV = "DOCMILL_HTTP_TOKEN"
LOOPBACK = "127.0.0.1"
HEALTH_PATH = "/healthz"
MCP_PATH = "/mcp"

# Fixed order so the health response is identical for identical state.
_ENGINES: tuple[type[McpEngineTool], ...] = (GridCraftTool, DeckCraftTool, WordCraftTool)


def http_token() -> str | None:
    """설정된 HTTP 인증 토큰. 없거나 빈 값이면 `None`."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    return token or None


def bind_host(requested: str, token: str | None) -> str:
    """실제로 바인딩할 주소. 토큰이 없으면 요청과 상관없이 127.0.0.1이다."""
    if token is None and requested != LOOPBACK:
        logger.warning(
            "%s가 설정되지 않아 %s 대신 %s에만 바인딩한다", TOKEN_ENV, requested, LOOPBACK
        )
        return LOOPBACK
    return requested


def engine_status() -> dict[str, bool]:
    """엔진별 준비 상태(실행 파일을 찾을 수 있으면 True)."""
    status: dict[str, bool] = {}
    for cls in _ENGINES:
        try:
            cls.locate()
        except EngineUnavailableError:
            status[cls.name] = False
        else:
            status[cls.name] = True
    return status


async def healthz(request: Request) -> Response:
    """`/healthz` 처리기. 모든 엔진이 준비되면 200, 아니면 503."""
    engines = engine_status()
    ready = all(engines.values())
    body = {"status": "ok" if ready else "unavailable", "engines": engines}
    return JSONResponse(body, status_code=200 if ready else 503)


class BearerTokenMiddleware:
    """`/healthz`를 뺀 HTTP 요청에 Bearer 토큰을 요구하는 ASGI 미들웨어."""

    def __init__(self, app: ASGIApp, token: str, public_paths: tuple[str, ...] = ()) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()
        self._public = public_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._public:
            await self.app(scope, receive, send)
            return
        if self._authorized(scope):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {"error": "인증 토큰이 없거나 올바르지 않습니다"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        for key, value in headers:
            if key.lower() == b"authorization":
                return hmac.compare_digest(value, self._expected)
        return False


def build_http_app(server: MCPServer, host: str, token: str | None) -> Any:
    """헬스 체크와(토큰이 있으면) 인증이 붙은 streamable HTTP ASGI 앱을 만든다."""
    server.custom_route(HEALTH_PATH, methods=["GET"])(healthz)
    app: ASGIApp = server.streamable_http_app(streamable_http_path=MCP_PATH, host=host)
    if token is not None:
        app = BearerTokenMiddleware(app, token, public_paths=(HEALTH_PATH,))
    return app


def serve_http(server: MCPServer, host: str, port: int) -> None:
    """HTTP 모드로 서버를 실행한다. 토큰이 없으면 127.0.0.1에만 바인딩한다."""
    import uvicorn

    token = http_token()
    actual = bind_host(host, token)
    app = build_http_app(server, actual, token)
    uvicorn.run(app, host=actual, port=port, log_level="info")
