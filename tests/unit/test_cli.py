from pathlib import Path

import pytest
from typer.testing import CliRunner

from docmill import __version__
from docmill.cli import app

runner = CliRunner()


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_run_unknown_recipe_exits_with_code_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    result = runner.invoke(app, ["run", "없는레시피", "--input", "a.xlsx"])
    assert result.exit_code == 1
    assert "실패" in result.output


def test_history_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from docmill.runlog import record_run

    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    for rid, result in (("a", '{"path": "p"}'), ("b", '{"error": "불일치", "step": "3"}')):
        record_run(
            run_id=rid,
            tool="run_recipe",
            args_summary="recipe=R",
            result=result,
            status="ok",
            duration_ms=1500,
            ai_used=False,
            recipe="R",
            home=tmp_path,
        )
    out = runner.invoke(app, ["history", "R"]).output
    for header in ("시간", "소요", "AI", "검증"):
        assert header in out
    assert "사용 안 함" in out and "통과" in out and "실패: 불일치" in out and "1.50" in out
    assert "없습니다" in runner.invoke(app, ["history", "none"]).output


def test_run_prints_result_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    monkeypatch.setattr(
        "docmill.runner.execute_recipe",
        lambda recipe, input, out_dir: {"path": "/tmp/result.pptx"},
    )
    result = runner.invoke(app, ["run", "r", "--input", "a.xlsx"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "/tmp/result.pptx"


def _capture_run(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    from mcp.server.mcpserver import MCPServer

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_run(self: MCPServer, *args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(MCPServer, "run", fake_run)
    return calls


def test_mcp_http_option_runs_streamable_http(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_run(monkeypatch)
    result = runner.invoke(app, ["mcp", "--http", "--port", "9123"])
    assert result.exit_code == 0
    assert calls == [(("streamable-http",), {"host": "127.0.0.1", "port": 9123})]


def test_mcp_default_runs_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_run(monkeypatch)
    assert runner.invoke(app, ["mcp"]).exit_code == 0
    assert calls == [((), {})]
