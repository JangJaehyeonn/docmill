"""docmill 명령줄 도구.

현재는 뼈대만 있다. 각 명령의 실제 동작은 GitHub 이슈(FR-xx)에서 구현한다.
"""

from __future__ import annotations

import typer

from docmill import __version__
from docmill.recipes import RecipeError, delete_recipe, list_recipes, rename_recipe

app = typer.Typer(
    name="docmill",
    help="엑셀·PPT·워드 작업을 레시피로 저장하고 AI 없이 반복 실행합니다.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def version() -> None:
    """설치된 docmill 버전을 출력합니다."""
    typer.echo(__version__)


@app.command()
def mcp() -> None:
    """AI 앱에 연결할 MCP 서버를 실행합니다 (stdio). FR-01."""
    from docmill.server import build_server

    build_server().run()


@app.command()
def run(
    recipe: str = typer.Argument(..., help="실행할 레시피 이름"),
    input_path: str = typer.Option(..., "--input", help="레시피에 넣을 입력 파일 경로"),
    out: str | None = typer.Option(None, "--out", help="결과를 저장할 폴더"),
) -> None:
    """레시피를 AI 없이 실행합니다. 성공하면 결과 파일 경로를 출력하고 종료 코드 0을 돌려줍니다."""
    import json

    from docmill.runlog import logged
    from docmill.runner import run_recipe
    from docmill.webhook import send_summary

    result = json.loads(logged(run_recipe, ai_used=False)(recipe, input_path, out))
    ok = "error" not in result
    summary = {"recipe": recipe, "status": "success" if ok else "failure"}
    summary.update({"path": result["path"]} if ok else {"error": str(result["error"])})
    send_summary(summary)
    if not ok:
        typer.echo(f"실패({result.get('step', 'unexpected')}): {result['error']}", err=True)
        raise typer.Exit(code=1)
    typer.echo(result["path"])


recipes_app = typer.Typer(help="저장된 레시피를 관리합니다.", invoke_without_command=True)
app.add_typer(recipes_app, name="recipes")


@recipes_app.callback()
def recipes(ctx: typer.Context) -> None:
    """저장된 레시피 목록을 보여줍니다. FR-13."""
    if ctx.invoked_subcommand is None:
        for name in list_recipes():
            typer.echo(name)


def _fail(exc: RecipeError) -> typer.Exit:
    typer.echo(f"실패({exc.step}): {exc.message}", err=True)
    return typer.Exit(code=1)


@recipes_app.command("rename")
def recipes_rename(
    old: str = typer.Argument(..., help="기존 레시피 이름"),
    new: str = typer.Argument(..., help="새 이름"),
) -> None:
    """레시피 이름을 바꿉니다."""
    try:
        typer.echo(f"이름 변경: {old} -> {rename_recipe(old, new)}")
    except RecipeError as exc:
        raise _fail(exc) from exc


@recipes_app.command("delete")
def recipes_delete(name: str = typer.Argument(..., help="삭제할 레시피 이름")) -> None:
    """레시피를 삭제합니다."""
    try:
        delete_recipe(name)
    except RecipeError as exc:
        raise _fail(exc) from exc
    typer.echo(f"삭제: {name}")


@app.command()
def history(recipe: str = typer.Argument(..., help="기록을 볼 레시피 이름")) -> None:
    """레시피 실행 기록을 보여줍니다. FR-17 (구현 예정)."""
    typer.echo("아직 구현되지 않았습니다: FR-17 이슈에서 구현합니다.", err=True)
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
