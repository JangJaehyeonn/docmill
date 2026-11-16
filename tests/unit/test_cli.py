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


def test_run_prints_result_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    monkeypatch.setattr(
        "docmill.runner.execute_recipe",
        lambda recipe, input, out_dir: {"path": "/tmp/result.pptx"},
    )
    result = runner.invoke(app, ["run", "r", "--input", "a.xlsx"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "/tmp/result.pptx"
