import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from docmill import evals
from docmill.cli import app
from docmill.evals import Answer, EvalError, Scenario

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[2]

REPORT = Scenario(
    "r1", "보고서 만들어줘", "excel_to_report_ppt", {"input": "a.xlsx", "value": "매출"}
)
NONE = Scenario("n1", "안녕", None, {})


def _write_scenarios(folder: Path, items: list[Scenario]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for number, s in enumerate(items):
        data = {"id": s.id, "request": s.request, "expected": {"tool": s.tool, "args": s.args}}
        (folder / f"{number:02d}.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


def test_repository_scenarios_cover_every_tool() -> None:
    scenarios = evals.load_scenarios(ROOT / "evals" / "scenarios")
    assert len(scenarios) >= 30
    for tool in [*evals.BUSINESS_TOOLS, None]:
        assert sum(1 for s in scenarios if s.tool == tool) >= 3
    assert all(s.args for s in scenarios if s.tool)


def test_load_rejects_unknown_tool_and_duplicates(tmp_path: Path) -> None:
    bad = Scenario("x", "?", "delete_everything", {})
    with pytest.raises(EvalError, match="알 수 없는 도구"):
        evals.load_scenarios(_write_scenarios(tmp_path / "a", [bad]))
    with pytest.raises(EvalError, match="중복"):
        evals.load_scenarios(_write_scenarios(tmp_path / "b", [NONE, NONE]))
    with pytest.raises(EvalError, match="없습니다"):
        evals.load_scenarios(tmp_path / "empty")


def test_score_tool_and_args() -> None:
    exact = evals.score(REPORT, Answer("excel_to_report_ppt", {**REPORT.args, "slides": 5}))
    assert exact["tool_match"] and exact["args_match"]
    full_path = Answer("excel_to_report_ppt", {"input": "/tmp/x/a.xlsx", "value": "매출"})
    assert evals.score(REPORT, full_path)["args_match"]
    wrong_args = evals.score(REPORT, Answer("excel_to_report_ppt", {"input": "a.xlsx"}))
    assert wrong_args["tool_match"] and not wrong_args["args_match"]
    wrong_tool = evals.score(REPORT, Answer("verify_numbers", REPORT.args))
    assert not wrong_tool["tool_match"] and not wrong_tool["args_match"]


def test_score_no_tool_and_errors() -> None:
    assert evals.score(NONE, Answer(None))["args_match"]
    assert not evals.score(NONE, Answer("save_recipe", {"name": "a"}))["tool_match"]
    assert not evals.score(NONE, Answer(None, error="timeout"))["tool_match"]


def test_evaluate_summarizes_per_tool() -> None:
    other = Scenario("r2", "또", "excel_to_report_ppt", {"input": "b.xlsx"})
    answers = {"r1": Answer("excel_to_report_ppt", {}), "r2": Answer(None), "n1": Answer(None)}
    report = evals.evaluate([REPORT, other, NONE], lambda s: answers[s.id], "fake", "2026-01-02")
    tools = report["tools"]
    assert list(tools) == ["excel_to_report_ppt", "none"]
    assert tools["excel_to_report_ppt"]["tool_rate"] == 0.5
    assert tools["excel_to_report_ppt"]["args_rate"] == 0.0
    assert tools["none"]["tool_rate"] == 1.0
    assert report["tool_rate"] == pytest.approx(2 / 3)


@pytest.mark.parametrize("date", ["2026-1-2", "2026-02-30", "../x", "20260102"])
def test_check_date_rejects_bad_values(date: str) -> None:
    with pytest.raises(EvalError):
        evals.check_date(date)


def test_write_report_is_deterministic(tmp_path: Path) -> None:
    report = evals.evaluate([REPORT, NONE], lambda s: Answer(s.tool, s.args), "fake", "2026-01-02")
    md_a, json_a = evals.write_report(report, tmp_path / "a")
    md_b, json_b = evals.write_report(report, tmp_path / "b")
    assert md_a.name == "2026-01-02.md"
    assert md_a.read_bytes() == md_b.read_bytes()
    assert json_a.read_bytes() == json_b.read_bytes()
    assert "| excel_to_report_ppt | 1 | 100.0% | 100.0% |" in md_a.read_text(encoding="utf-8")


def test_compare_lines_format() -> None:
    before = {
        "date": "a",
        "tools": {"apply_template": {"tool_rate": 2 / 3}, "none": {"tool_rate": 1.0}},
    }
    after = {
        "date": "b",
        "tools": {"apply_template": {"tool_rate": 1.0}, "run_recipe": {"tool_rate": 0.5}},
    }
    lines = evals.compare_lines(before, after)
    assert "apply_template 66.7% -> 100.0% (+33.3)" in [ln.replace("  ", " ") for ln in lines]
    assert any(ln.startswith("run_recipe") and "- -> 50.0%" in ln for ln in lines)
    assert any(ln.startswith("none") and "100.0% -> -" in ln for ln in lines)
    same = evals.compare_lines(after, after)
    assert all("-0.0" not in ln for ln in same)


def test_model_env_drops_anthropic_credentials() -> None:
    env = evals.model_env({"ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t", "PATH": "/bin"})
    assert env == {"PATH": "/bin"}


def _stream(*events: dict[str, Any]) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\nnot json\n"


def test_parse_stream_finds_first_docmill_tool() -> None:
    other = {"type": "tool_use", "name": "Bash", "input": {}}
    mine = {"type": "tool_use", "name": "mcp__docmill__run_recipe", "input": {"recipe": "a"}}
    text = _stream(
        {"type": "system"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}, other]}},
        {"type": "assistant", "message": {"content": [mine]}},
    )
    assert evals.parse_stream(text) == Answer("run_recipe", {"recipe": "a"})
    assert evals.parse_stream(_stream({"type": "result"})) == Answer(None)


def test_claude_code_model_uses_cli_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["cmd"], seen["env"] = cmd, kwargs["env"]
        config = json.loads(Path(cmd[cmd.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
        seen["config"] = config
        block = {"type": "tool_use", "name": "mcp__docmill__save_recipe", "input": {"name": "a"}}
        out = _stream({"type": "assistant", "message": {"content": [block]}})
        return subprocess.CompletedProcess(cmd, 0, out, "")

    answer = evals.claude_code_model(fake_run)(NONE)
    assert answer == Answer("save_recipe", {"name": "a"})
    assert seen["cmd"][:3] == ["claude", "-p", NONE.request]
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert "docmill" in seen["config"]["mcpServers"]


def test_claude_code_model_reports_failures() -> None:
    def failing(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, "", "auth")

    def slow(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd, 1)

    def missing(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("claude")

    assert evals.claude_code_model(failing)(NONE).error is not None
    assert evals.claude_code_model(slow)(NONE).error is not None
    with pytest.raises(EvalError, match="claude"):
        evals.claude_code_model(missing)(NONE)


def test_cli_fake_eval_and_compare(tmp_path: Path) -> None:
    folder = _write_scenarios(tmp_path / "s", [REPORT, NONE])
    responses = tmp_path / "resp.json"
    responses.write_text(json.dumps({"r1": {"tool": "verify_numbers"}}), encoding="utf-8")
    base = ["eval", "--model", "fake", "--responses", str(responses), "--scenarios", str(folder)]
    first = runner.invoke(app, [*base, "--out-dir", str(tmp_path / "o"), "--date", "2026-01-01"])
    assert first.exit_code == 0, first.output
    responses.write_text(json.dumps({"r1": {"tool": "excel_to_report_ppt"}}), encoding="utf-8")
    second = runner.invoke(app, [*base, "--out-dir", str(tmp_path / "o"), "--date", "2026-01-02"])
    assert second.exit_code == 0, second.output
    old, new = tmp_path / "o" / "2026-01-01.json", tmp_path / "o" / "2026-01-02.json"
    result = runner.invoke(app, ["eval", "--compare", str(old), str(new)])
    assert result.exit_code == 0, result.output
    assert "excel_to_report_ppt 0.0% -> 100.0% (+100.0)" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["--model", "fake", "--responses", "r.json"],
        ["--model", "gpt", "--date", "2026-01-01"],
        ["--model", "fake", "--date", "2026-01-01"],
        ["--compare", "missing-a.json", "missing-b.json"],
    ],
)
def test_cli_eval_errors_exit_1(args: list[str]) -> None:
    result = runner.invoke(app, ["eval", *args])
    assert result.exit_code == 1
    assert "실패" in result.output
