"""docmill MCP 서버.

AI 앱에는 업무 단위 도구만 노출한다. 엔진의 저수준 도구는 서버 내부에서만 쓴다.
지금은 연결 확인용 `ping` 도구만 있고, 업무 도구는 FR-05 ~ FR-10에서 추가한다.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from docmill import __version__

SERVER_NAME = "docmill"


def build_server() -> MCPServer:
    """도구가 등록된 MCP 서버를 만든다."""
    server = MCPServer(SERVER_NAME)

    @server.tool()
    def ping() -> str:
        """서버 연결을 확인한다. 연결되면 docmill 버전을 돌려준다."""
        return f"docmill {__version__} 연결됨"

    return server
