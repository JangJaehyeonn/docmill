from typer.testing import CliRunner

from docmill import __version__
from docmill.cli import app

runner = CliRunner()


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_unimplemented_run_exits_with_code_2() -> None:
    result = runner.invoke(app, ["run", "주간 매출 보고", "--input", "a.xlsx"])
    assert result.exit_code == 2
