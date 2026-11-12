import asyncio

from docmill.server import build_server


def test_every_registrar_is_applied_and_only_business_tools_exposed() -> None:
    from docmill import server as server_module

    allowed = {
        "ping",
        "excel_to_report_ppt",
        "doc_to_task_table",
        "apply_template",
        "verify_numbers",
        "save_recipe",
        "run_recipe",
    }
    names = {tool.name for tool in asyncio.run(server_module.build_server().list_tools())}
    assert server_module.TOOL_REGISTRARS
    assert names <= allowed


def test_server_exposes_ping_tool() -> None:
    server = build_server()
    tools = asyncio.run(server.list_tools())
    assert "ping" in {tool.name for tool in tools}
