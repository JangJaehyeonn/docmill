"""docmill 명령줄 도구.

현재는 뼈대만 있다. 각 명령의 실제 동작은 GitHub 이슈(FR-xx)에서 구현한다.
"""

from __future__ import annotations

import typer

from docmill import __version__

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
    """레시피를 AI 없이 실행합니다. FR-10, FR-14 (구현 예정)."""
    typer.echo("아직 구현되지 않았습니다: FR-10 / FR-14 이슈에서 구현합니다.", err=True)
    raise typer.Exit(code=2)


@app.command()
def history(recipe: str = typer.Argument(..., help="기록을 볼 레시피 이름")) -> None:
    """레시피 실행 기록을 보여줍니다. FR-17 (구현 예정)."""
    typer.echo("아직 구현되지 않았습니다: FR-17 이슈에서 구현합니다.", err=True)
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
