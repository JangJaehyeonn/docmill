import asyncio

from docmill.server import build_server


def test_server_exposes_ping_tool() -> None:
    server = build_server()
    tools = asyncio.run(server.list_tools())
    assert "ping" in {tool.name for tool in tools}
