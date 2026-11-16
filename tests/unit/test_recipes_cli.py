import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docmill.cli import app
from docmill.recipes import (
    RecipeError,
    delete_recipe,
    list_recipes,
    recipes_dir,
    rename_recipe,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))


def _write(name: str) -> None:
    recipes_dir().mkdir(parents=True, exist_ok=True)
    text = json.dumps({"name": name, "steps": []})
    (recipes_dir() / f"{name}.json").write_text(text, encoding="utf-8")


def test_list_rename_delete_functions() -> None:
    assert list_recipes() == []
    _write("b")
    _write("a")
    assert list_recipes() == ["a", "b"]
    assert rename_recipe("a", "c") == "c"
    assert list_recipes() == ["b", "c"]
    stored = json.loads((recipes_dir() / "c.json").read_text(encoding="utf-8"))
    assert stored["name"] == "c"
    with pytest.raises(RecipeError):
        rename_recipe("b", "c")
    with pytest.raises(RecipeError):
        rename_recipe("zzz", "d")
    delete_recipe("b")
    assert list_recipes() == ["c"]
    with pytest.raises(RecipeError):
        delete_recipe("b")


def test_cli_list_rename_delete() -> None:
    _write("a")
    assert runner.invoke(app, ["recipes"]).stdout.split() == ["a"]
    assert runner.invoke(app, ["recipes", "rename", "a", "b"]).exit_code == 0
    assert runner.invoke(app, ["recipes"]).stdout.split() == ["b"]
    assert runner.invoke(app, ["recipes", "delete", "nope"]).exit_code == 1
    assert runner.invoke(app, ["recipes", "delete", "b"]).exit_code == 0
    assert runner.invoke(app, ["recipes"]).stdout.strip() == ""
