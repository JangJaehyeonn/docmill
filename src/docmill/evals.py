"""도구 선택 평가 (FR-23).

`evals/scenarios/*.json`의 사용자 요청을 모델에 보내고, 모델이 고른 docmill 도구와 핵심 인자가
기대값과 맞는지 채점해 날짜별 보고서(md·json)로 남긴다. 실제 모델은 Claude Code CLI(구독 인증)로만
호출하고 Anthropic API 키 환경 변수는 자식 프로세스에 넘기지 않는다.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BUSINESS_TOOLS: tuple[str, ...] = (
    "excel_to_report_ppt",
    "doc_to_task_table",
    "apply_template",
    "verify_numbers",
    "save_recipe",
    "run_recipe",
)
NO_TOOL = "none"
MCP_PREFIX = "mcp__docmill__"
CLAUDE_TIMEOUT_SECONDS = 180
# Credentials that would bypass subscription auth; never forwarded to the model process.
_BLOCKED_ENV_PREFIXES = ("ANTHROPIC_",)


class EvalError(Exception):
    """평가 실행이 실패했을 때 실패한 단계와 원인을 담는다."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


@dataclass(frozen=True)
class Scenario:
    """평가 시나리오 하나: 사용자 요청과 기대 도구·핵심 인자."""

    id: str
    request: str
    tool: str | None
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Answer:
    """모델이 요청에 대해 고른 도구와 인자 (도구를 부르지 않았으면 tool은 None)."""

    tool: str | None
    args: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


Model = Callable[[Scenario], Answer]


def load_scenarios(folder: Path) -> list[Scenario]:
    """폴더의 시나리오 JSON을 파일 이름 순으로 읽는다."""
    files = sorted(folder.glob("*.json"))
    if not files:
        raise EvalError("scenarios", f"시나리오 파일이 없습니다: {folder}")
    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for path in files:
        scenario = _parse_scenario(path)
        if scenario.id in seen:
            raise EvalError("scenarios", f"시나리오 id가 중복됩니다: {scenario.id}")
        seen.add(scenario.id)
        scenarios.append(scenario)
    return scenarios


def _parse_scenario(path: Path) -> Scenario:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        expected = data["expected"]
        tool = expected["tool"]
        args = expected.get("args") or {}
        sid, request = str(data["id"]), str(data["request"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise EvalError("scenarios", f"시나리오를 읽을 수 없습니다: {path.name}: {exc}") from exc
    if tool is not None and tool not in BUSINESS_TOOLS:
        raise EvalError("scenarios", f"알 수 없는 도구입니다: {path.name}: {tool}")
    if not isinstance(args, dict):
        raise EvalError("scenarios", f"args는 객체여야 합니다: {path.name}")
    return Scenario(id=sid, request=request, tool=tool, args=args)


def _to_answer(raw: Any) -> Answer:
    if not isinstance(raw, dict):
        return Answer(tool=None)
    tool = raw.get("tool")
    args = raw.get("args")
    return Answer(tool=str(tool) if tool else None, args=args if isinstance(args, dict) else {})


def fake_model(responses_path: Path) -> Model:
    """시나리오 id별로 미리 정한 응답을 돌려주는 가짜 모델을 만든다 (테스트·CI용)."""
    try:
        responses = json.loads(responses_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvalError("responses", f"응답 파일을 읽을 수 없습니다: {exc}") from exc
    if not isinstance(responses, dict):
        raise EvalError("responses", "응답 파일은 {시나리오 id: 응답} 객체여야 합니다")

    def answer(scenario: Scenario) -> Answer:
        return _to_answer(responses.get(scenario.id))

    return answer


def model_env(environ: Mapping[str, str]) -> dict[str, str]:
    """모델 프로세스에 넘길 환경 변수. API 키 등 ANTHROPIC_* 값은 모두 뺀다."""
    return {k: v for k, v in environ.items() if not k.startswith(_BLOCKED_ENV_PREFIXES)}


def mcp_config(docmill_home: str) -> dict[str, Any]:
    """Claude Code에 docmill MCP 서버(stdio)를 연결하는 설정을 만든다."""
    server = {
        "command": sys.executable,
        "args": ["-m", "docmill.cli", "mcp"],
        "env": {"DOCMILL_HOME": docmill_home},
    }
    return {"mcpServers": {"docmill": server}}


def claude_command(request: str, config_path: str) -> list[str]:
    """시나리오 하나를 Claude Code CLI 비대화 모드로 보내는 명령을 만든다.

    docmill 도구는 허용 목록에 넣지 않으므로 실행은 거부되고, 모델이 고른 호출만 기록된다.
    """
    return [
        "claude",
        "-p",
        request,
        "--mcp-config",
        config_path,
        "--strict-mcp-config",
        "--output-format",
        "stream-json",
        "--verbose",
    ]


def parse_stream(stdout: str) -> Answer:
    """Claude Code stream-json 출력에서 처음 고른 docmill 도구 호출을 찾는다."""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        content = (event.get("message") or {}).get("content") or []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name", ""))
            if name.startswith(MCP_PREFIX):
                return _to_answer({"tool": name[len(MCP_PREFIX) :], "args": block.get("input")})
    return Answer(tool=None)


def claude_code_model(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Model:
    """docmill MCP 서버를 연결한 Claude Code CLI(구독 인증)를 모델로 쓴다."""

    def answer(scenario: Scenario) -> Answer:
        with tempfile.TemporaryDirectory(prefix="docmill-eval-") as work:
            config = Path(work) / "mcp.json"
            home = Path(work) / "home"
            config.write_text(json.dumps(mcp_config(str(home))), encoding="utf-8")
            try:
                proc = run(
                    claude_command(scenario.request, str(config)),
                    cwd=work,
                    env=model_env(os.environ),
                    capture_output=True,
                    text=True,
                    timeout=CLAUDE_TIMEOUT_SECONDS,
                )
            except FileNotFoundError as exc:
                raise EvalError("model", "claude CLI를 찾을 수 없습니다") from exc
            except subprocess.TimeoutExpired:
                return Answer(tool=None, error=f"{CLAUDE_TIMEOUT_SECONDS}초 안에 응답이 없습니다")
        found = parse_stream(proc.stdout)
        if found.tool is None and proc.returncode != 0:
            return Answer(tool=None, error=f"claude 종료 코드 {proc.returncode}")
        return found

    return answer


def _value_matches(expected: Any, actual: Any) -> bool:
    if expected == actual:
        return True
    if isinstance(expected, str) and isinstance(actual, str):
        # Models often pass a full path; the scenario only names the file.
        return actual.replace("\\", "/").rsplit("/", 1)[-1] == expected
    return False


def score(scenario: Scenario, answer: Answer) -> dict[str, Any]:
    """시나리오 하나의 도구 일치·인자 일치 여부를 계산한다."""
    tool_match = answer.error is None and answer.tool == scenario.tool
    args_match = tool_match and all(
        key in answer.args and _value_matches(value, answer.args[key])
        for key, value in scenario.args.items()
    )
    return {
        "expected_tool": scenario.tool,
        "actual_tool": answer.tool,
        "tool_match": tool_match,
        "args_match": args_match,
        "error": answer.error,
    }


def _tool_key(tool: str | None) -> str:
    return tool if tool is not None else NO_TOOL


def summarize(rows: Mapping[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """시나리오 결과를 기대 도구별 개수·도구 일치율·인자 일치율로 묶는다."""
    order = [*BUSINESS_TOOLS, NO_TOOL]
    tools: dict[str, dict[str, Any]] = {}
    for key in order:
        mine = [r for r in rows.values() if _tool_key(r["expected_tool"]) == key]
        if not mine:
            continue
        tool_ok = sum(1 for r in mine if r["tool_match"])
        args_ok = sum(1 for r in mine if r["args_match"])
        tools[key] = {
            "count": len(mine),
            "tool_match": tool_ok,
            "args_match": args_ok,
            "tool_rate": tool_ok / len(mine),
            "args_rate": args_ok / len(mine),
        }
    return tools


def check_date(date: str) -> str:
    """보고서 날짜가 실제 있는 YYYY-MM-DD인지 확인하고 그대로 돌려준다."""
    try:
        parsed = datetime.date.fromisoformat(date)
    except ValueError as exc:
        raise EvalError("date", f"날짜는 YYYY-MM-DD 형식이어야 합니다: {date}") from exc
    if parsed.isoformat() != date:
        raise EvalError("date", f"날짜는 YYYY-MM-DD 형식이어야 합니다: {date}")
    return date


def evaluate(scenarios: list[Scenario], model: Model, model_name: str, date: str) -> dict[str, Any]:
    """모든 시나리오를 모델에 보내고 채점한 보고서 데이터를 만든다."""
    check_date(date)
    rows = {s.id: score(s, model(s)) for s in scenarios}
    total = len(rows)
    return {
        "date": date,
        "model": model_name,
        "total": total,
        "tool_rate": sum(1 for r in rows.values() if r["tool_match"]) / total,
        "args_rate": sum(1 for r in rows.values() if r["args_match"]) / total,
        "tools": summarize(rows),
        "scenarios": rows,
    }


def _pct(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def render_markdown(report: Mapping[str, Any]) -> str:
    """보고서 데이터를 사람이 읽는 마크다운으로 바꾼다."""
    lines = [
        f"# 도구 선택 평가 {report['date']}",
        "",
        f"- 모델: {report['model']}",
        f"- 시나리오: {report['total']}개",
        f"- 도구 일치율: {_pct(report['tool_rate'])}",
        f"- 인자 일치율: {_pct(report['args_rate'])}",
        "",
        "## 도구별",
        "",
        "| 기대 도구 | 시나리오 | 도구 일치율 | 인자 일치율 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, row in report["tools"].items():
        lines.append(
            f"| {name} | {row['count']} | {_pct(row['tool_rate'])} | {_pct(row['args_rate'])} |"
        )
    lines += [
        "",
        "## 시나리오별",
        "",
        "| id | 기대 도구 | 고른 도구 | 도구 | 인자 |",
        "| --- | --- | --- | :---: | :---: |",
    ]
    for sid, row in report["scenarios"].items():
        mark = {True: "O", False: "X"}
        lines.append(
            f"| {sid} | {_tool_key(row['expected_tool'])} | {_tool_key(row['actual_tool'])} "
            f"| {mark[row['tool_match']]} | {mark[row['args_match']]} |"
        )
    return "\n".join(lines) + "\n"


def write_report(report: Mapping[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """보고서를 `<out_dir>/<날짜>.md`와 `.json`으로 저장한다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{report['date']}.md"
    json_path = out_dir / f"{report['date']}.json"
    md_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    json_path.write_text(text, encoding="utf-8", newline="\n")
    return md_path, json_path


def load_report(path: Path) -> dict[str, Any]:
    """저장된 JSON 보고서를 읽는다."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        tools = data["tools"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise EvalError("compare", f"보고서를 읽을 수 없습니다: {path}: {exc}") from exc
    if not isinstance(tools, dict):
        raise EvalError("compare", f"보고서의 tools 형식이 잘못됐습니다: {path}")
    return dict(data)


def compare_lines(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """두 보고서의 도구별 도구 일치율 변화를 한 줄씩 만든다.

    예: `apply_template 66.7% -> 100.0% (+33.3)`. 한쪽에만 있는 도구는 `-`로 표시한다.
    """
    old_tools, new_tools = before["tools"], after["tools"]
    known = [*BUSINESS_TOOLS, NO_TOOL]
    extra = sorted((set(old_tools) | set(new_tools)) - set(known))
    names = [n for n in [*known, *extra] if n in old_tools or n in new_tools]
    width = max((len(n) for n in names), default=0)
    lines = [f"도구 일치율 비교: {before.get('date', '?')} -> {after.get('date', '?')}"]
    for name in names:
        old = old_tools.get(name, {}).get("tool_rate")
        new = new_tools.get(name, {}).get("tool_rate")
        old_text = _pct(old) if old is not None else "-"
        new_text = _pct(new) if new is not None else "-"
        delta = ""
        if old is not None and new is not None:
            delta = f" ({round((new - old) * 100, 1) + 0.0:+.1f})"
        lines.append(f"{name.ljust(width)} {old_text} -> {new_text}{delta}")
    return lines
