"""docmill 명령줄 도구.

현재는 뼈대만 있다. 각 명령의 실제 동작은 GitHub 이슈(FR-xx)에서 구현한다.
"""

from __future__ import annotations

from typing import Any

import typer

from docmill import __version__
from docmill.recipes import RecipeError, delete_recipe, list_recipes, rename_recipe, show_recipe

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
def mcp(
    http: bool = typer.Option(False, "--http", help="stdio 대신 HTTP MCP 서버로 실행합니다."),
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP 모드에서 바인딩할 주소"),
    port: int = typer.Option(8000, "--port", help="HTTP 모드에서 열 포트"),
) -> None:
    """AI 앱에 연결할 MCP 서버를 실행합니다 (기본 stdio, --http면 원격 HTTP). FR-01, FR-19.

    HTTP 모드는 DOCMILL_HTTP_TOKEN이 있으면 Bearer 토큰을 요구하고, 없으면 127.0.0.1에만
    바인딩합니다. /healthz로 엔진 준비 상태를 알려줍니다. FR-24.
    """
    from docmill.server import build_server

    server = build_server()
    if http:
        from docmill.remote import serve_http

        serve_http(server, host, port)
    else:
        server.run()


@app.command()
def suggest(path: str = typer.Argument(..., help="제안을 받을 문서 경로")) -> None:
    """열린 문서로 할 수 있는 작업 제안을 JSON으로 출력합니다 (studio가 쓴다). FR-30."""
    from docmill.tools.suggest import suggest_actions

    typer.echo(suggest_actions(path))


@app.command("similar")
def similar_command(
    workspace: str = typer.Argument(..., help="작업 폴더"),
    query: str = typer.Argument(..., help="시작할 작업 이름"),
) -> None:
    """작업 폴더에서 비슷한 예전 문서를 JSON으로 출력합니다 (studio가 쓴다). FR-48."""
    from docmill.tools.similar import find_similar_documents

    typer.echo(find_similar_documents(workspace, query))


@app.command("preflight")
def preflight_command(
    path: str = typer.Argument(..., help="점검할 문서 경로"),
    workspace: str = typer.Option("", help="설정 파일(.docmill/settings.json)이 있는 작업 폴더"),
) -> None:
    """문서를 보내기 전 안전 점검 결과를 JSON으로 출력합니다 (studio가 쓴다). FR-34."""
    from docmill.runlog import logged
    from docmill.tools.preflight import preflight

    typer.echo(logged(preflight, ai_used=False)(path, workspace=workspace or None))


@app.command("export-doc")
def export_doc_command(
    path: str = typer.Argument(..., help="내보낼 문서 경로"),
    kind: str = typer.Option(..., "--kind", help="mail(메일 초안 .eml) 또는 folder(폴더에 저장)"),
    folder: str = typer.Option("", "--folder", help="저장할 폴더 (작업 폴더 안의 상대 경로)"),
    workspace: str = typer.Option("", "--workspace", help="작업 폴더"),
) -> None:
    """문서를 폴더에 저장하거나 메일 초안(.eml)으로 만들고 결과를 JSON으로 출력합니다. FR-46."""
    from docmill.runlog import logged
    from docmill.tools.export import export_document

    out = logged(export_document, ai_used=False)(path, kind, folder, "", workspace or None)
    typer.echo(out)


@app.command("refresh-links")
def refresh_links_command(
    path: str = typer.Argument(..., help="새 숫자로 고칠 문서 경로"),
    workspace: str | None = typer.Option(
        None, "--workspace", help="이 폴더 안의 문서·출처 파일·원본만 읽습니다 (studio가 쓴다)."
    ),
) -> None:
    """연결된 원본 엑셀의 최신 값으로 문서 숫자를 고치고 결과를 JSON으로 출력합니다. FR-37."""
    from docmill.runlog import logged
    from docmill.tools.refresh import refresh_links_within

    def refresh_links(document: str) -> str:
        return refresh_links_within(document, workspace)

    typer.echo(logged(refresh_links, ai_used=False)(path))


@app.command("insert-range")
def insert_range_command(
    document: str = typer.Argument(..., help="표를 넣을 pptx 문서 경로"),
    source: str = typer.Argument(..., help="원본 xlsx 경로"),
    cell_range: str = typer.Argument(..., help="넣을 셀 범위 (예: A1:C16)"),
    sheet: str = typer.Option("", "--sheet", help="범위를 읽을 시트 이름 (비우면 첫 시트)"),
    receipt: str = typer.Option(
        "", "--receipt", help="발행을 마치면 완료 표시를 쓸 파일 (studio가 응답을 잃었을 때 쓴다)"
    ),
) -> None:
    """엑셀 셀 범위를 pptx에 표로 넣고 숫자 출처를 기록해 결과를 JSON으로 출력합니다. FR-44."""
    import functools
    import json

    from docmill.runlog import logged
    from docmill.tools.dropin import insert_range, insert_range_with_receipt

    class _Failed(Exception):
        """도구가 오류 JSON을 돌려줬음을 실행 로그에 `error`로 남기기 위한 예외."""

    @functools.wraps(insert_range)
    def failing_on_error(document: str, source: str, cell_range: str, sheet: str) -> str:
        result = insert_range_with_receipt(
            document, source, cell_range, sheet or None, receipt or None
        )
        if "error" in json.loads(result):
            raise _Failed(result)
        return result

    try:
        typer.echo(logged(failing_on_error, ai_used=False)(document, source, cell_range, sheet))
    except _Failed as failed:
        typer.echo(str(failed))  # studio reads the error JSON from stdout


@app.command("recover-insert")
def recover_insert_command(
    document: str = typer.Argument(..., help="끊긴 삽입을 되돌릴 pptx 문서 경로"),
    receipt: str = typer.Option(
        "",
        "--receipt",
        help="응답을 잃은 insert-range에 넘긴 완료 표시 경로 (그 호출의 결과인지 확인)",
    ),
) -> None:
    """끊긴 범위 넣기(문서만 바뀌고 출처는 옛것)를 되돌리고 결과를 JSON으로 출력합니다. FR-44."""
    import functools
    import json

    from docmill.runlog import logged
    from docmill.tools.dropin import recover_insert

    class _Failed(Exception):
        """도구가 오류 JSON을 돌려줬음을 실행 로그에 `error`로 남기기 위한 예외."""

    @functools.wraps(recover_insert)
    def failing_on_error(document: str) -> str:
        result = recover_insert(document, receipt or None)
        if "error" in json.loads(result):
            raise _Failed(result)
        return result

    try:
        typer.echo(logged(failing_on_error, ai_used=False)(document))
    except _Failed as failed:
        typer.echo(str(failed))  # studio reads the error JSON from stdout


@app.command("mask-doc")
def mask_doc_command() -> None:
    """stdin의 JSON 요청(문서 경로·메시지·이름·대응표)을 가려 JSON으로 출력합니다. FR-39."""
    import json
    import sys

    from docmill.tools.mask import mask_documents

    request = json.loads(sys.stdin.read() or "{}")
    typer.echo(json.dumps(mask_documents(request), ensure_ascii=False))


@app.command("unmask-doc")
def unmask_doc_command(path: str = typer.Argument(..., help="자리표를 되돌릴 문서 경로")) -> None:
    """stdin의 대응표(JSON)로 문서 안의 자리표를 원래 값으로 되돌립니다 (studio가 쓴다). FR-39."""
    import json
    import sys
    from pathlib import Path

    from docmill.tools.mask import unmask_document

    table = json.loads(sys.stdin.read() or "{}")
    typer.echo(json.dumps({"restored": unmask_document(Path(path), table)}))


@app.command()
def setup() -> None:
    """고정된 버전의 엔진(GridCraft·DeckCraft·WordCraft)을 설치하고 버전을 출력합니다. FR-04."""
    from docmill.engines.install import ENGINES, EngineInstallError, ensure_installed

    failed = False
    for spec in ENGINES:
        try:
            version, downloaded = ensure_installed(spec)
        except (EngineInstallError, OSError) as exc:
            typer.echo(f"실패({spec.display}): {exc}", err=True)
            failed = True
            continue
        note = "설치함" if downloaded else "이미 설치됨"
        typer.echo(f"{spec.display}: {version} ({note})")
    if failed:
        raise typer.Exit(code=1)


_MAP_OPTION = typer.Option(
    None, "--map", help="바뀐 열 이름을 원래 열로 대응합니다 (새 열=원래 열, 여러 번 가능)"
)


@app.command()
def run(
    recipe: str = typer.Argument(..., help="실행할 레시피 이름"),
    input_path: str = typer.Option(..., "--input", help="레시피에 넣을 입력 파일 경로"),
    out: str | None = typer.Option(None, "--out", help="결과를 저장할 폴더"),
    column_map: list[str] | None = _MAP_OPTION,
) -> None:
    """레시피를 AI 없이 실행합니다. 성공하면 결과 파일 경로를 출력하고 종료 코드 0을 돌려줍니다.

    입력 열 구조가 레시피와 다르면 결과 파일을 쓰지 않고 열 매핑 제안을 JSON으로 출력합니다. FR-22.
    """
    import json

    from docmill.inputschema import SchemaError, parse_column_map, using_column_map
    from docmill.runlog import logged
    from docmill.runner import run_recipe
    from docmill.webhook import send_summary

    try:
        mapping = parse_column_map(column_map or [])
    except SchemaError as exc:
        typer.echo(f"실패(map): {exc}", err=True)
        raise typer.Exit(code=2) from exc
    with using_column_map(mapping):
        result = json.loads(logged(run_recipe, ai_used=False)(recipe, input_path, out))
    ok = "error" not in result
    summary = {"recipe": recipe, "status": "success" if ok else "failure"}
    summary.update({"path": result["path"]} if ok else {"error": str(result["error"])})
    send_summary(summary)
    if not ok:
        if "suggestions" in result:
            keys = ("error", "step", "missing", "suggestions")
            typer.echo(json.dumps({k: result[k] for k in keys}, ensure_ascii=False, indent=2))
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


@recipes_app.command("show")
def recipes_show(
    name: str = typer.Argument(..., help="볼 레시피 이름"),
    as_json: bool = typer.Option(False, "--json", help="레시피 내용을 JSON 그대로 출력합니다"),
) -> None:
    """레시피의 단계와 저장된 입력 구조(시트·열 이름·열 형식)를 보여줍니다. FR-22."""
    import json

    try:
        data = show_recipe(name)
    except RecipeError as exc:
        raise _fail(exc) from exc
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    typer.echo(f"레시피: {data.get('name', name)}")
    for number, step in enumerate(data.get("steps", []), start=1):
        typer.echo(
            f"{number}. {step.get('tool')} {json.dumps(step.get('args', {}), ensure_ascii=False)}"
        )
    schema = data.get("input_schema")
    if isinstance(schema, dict):
        typer.echo(f"시트: {', '.join(schema.get('sheets', []))}")
        types = schema.get("column_types", {})
        columns = [f"{c}({types.get(c, '?')})" for c in schema.get("columns", [])]
        typer.echo(f"열: {', '.join(columns)}")


@recipes_app.command("delete")
def recipes_delete(name: str = typer.Argument(..., help="삭제할 레시피 이름")) -> None:
    """레시피를 삭제합니다."""
    try:
        delete_recipe(name)
    except RecipeError as exc:
        raise _fail(exc) from exc
    typer.echo(f"삭제: {name}")


def _verdict(record: dict[str, Any]) -> str:
    """로그 한 건의 검증 결과를 '통과' 또는 '실패: 원인'으로 바꾼다."""
    import json
    import re

    text = str(record["result"])
    error: str | None = None
    if record["status"] != "ok":
        error = text
    else:
        try:
            data = json.loads(text)
            error = str(data["error"]) if isinstance(data, dict) and "error" in data else None
        except ValueError:  # result was clipped; look for the error text directly
            found = re.search(r'"error": "([^"]*)', text)
            error = found.group(1) if found else None
    return "통과" if error is None else f"실패: {error}"


@app.command()
def history(recipe: str = typer.Argument(..., help="기록을 볼 레시피 이름")) -> None:
    """레시피 실행 기록을 시간·소요 시간·AI 사용 여부·검증 결과 표로 보여줍니다. FR-17."""
    from docmill.runlog import list_recipe_runs

    records = list_recipe_runs(recipe)
    if not records:
        typer.echo(f"실행 기록이 없습니다: {recipe}")
        return
    rows = [("시간", "소요(초)", "AI", "검증")]
    for rec in records:
        ai = "사용" if rec["ai_used"] else "사용 안 함"
        seconds = f"{int(rec['duration_ms']) / 1000:.2f}"
        rows.append((str(rec["started_at"]), seconds, ai, _verdict(rec)))
    for row in rows:
        typer.echo(" | ".join(row))


_EVAL_MODELS = ("claude-code", "fake")
_COMPARE_OPTION = typer.Option(
    None, "--compare", help="두 JSON 보고서(이전, 새)의 도구별 정확도 변화를 출력합니다"
)


@app.command("eval")
def eval_tools(
    model: str = typer.Option("claude-code", "--model", help="평가할 모델 (claude-code 또는 fake)"),
    date: str | None = typer.Option(None, "--date", help="보고서 날짜 (YYYY-MM-DD)"),
    scenarios: str = typer.Option("evals/scenarios", "--scenarios", help="시나리오 폴더"),
    out_dir: str = typer.Option("evals/reports", "--out-dir", help="보고서를 저장할 폴더"),
    responses: str | None = typer.Option(
        None, "--responses", help="fake 모델의 시나리오별 응답 JSON"
    ),
    compare: tuple[str, str] | None = _COMPARE_OPTION,
) -> None:
    """도구 선택·인자 정확도를 평가해 보고서를 남기거나, 두 보고서를 비교합니다. FR-23.

    실제 모델은 Claude Code CLI(구독 인증)로만 호출하며 Anthropic API 키는 쓰지 않습니다.
    """
    from pathlib import Path

    from docmill import evals

    try:
        if compare is not None:
            before, after = (evals.load_report(Path(p)) for p in compare)
            for line in evals.compare_lines(before, after):
                typer.echo(line)
            return
        if date is None:
            raise evals.EvalError("date", "--date YYYY-MM-DD 로 보고서 날짜를 주세요")
        evals.check_date(date)
        if model not in _EVAL_MODELS:
            raise evals.EvalError("model", f"지원하지 않는 모델입니다: {model}")
        if model == "fake":
            if responses is None:
                raise evals.EvalError("responses", "fake 모델은 --responses 가 필요합니다")
            chosen = evals.fake_model(Path(responses))
        else:
            chosen = evals.claude_code_model()
        report = evals.evaluate(evals.load_scenarios(Path(scenarios)), chosen, model, date)
        for path in evals.write_report(report, Path(out_dir)):
            typer.echo(str(path))
    except evals.EvalError as exc:
        typer.echo(f"실패({exc.step}): {exc.message}", err=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
