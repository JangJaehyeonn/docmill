"""FR-23 도구 선택 평가 세트 인수 테스트 (초안).

필요한 샘플: 없음 (시나리오는 `evals/scenarios/`에 구현이 만든다).
가정(구현 방법이 정해지면 맞춰 고친다):
- 시나리오는 `evals/scenarios/*.json` 한 파일에 하나. 항목: `id`, `request`,
  `expected`: `{"tool": 도구 이름 또는 null, "args": {핵심 인자}}`.
- `docmill eval --model fake --responses <json> --scenarios <폴더> --out-dir <폴더>
  --date YYYY-MM-DD` 로 가짜 모델을 쓴다. responses는 `{시나리오 id: {"tool":..., "args":...}}`
  (도구 없음 응답은 `{"tool": null}`).
- 보고서: `<out-dir>/<날짜>.md`, `<날짜>.json`. JSON은 `scenarios`(id → `tool_match`,
  `args_match` 불리언)와 `tools`(도구 이름 → `tool_rate`, `args_rate`)를 가진다.
  도구 없음 시나리오는 `tools`에서 키 `none`으로 센다.
- `docmill eval --compare 이전.json 새.json`은 도구별 한 줄씩 이전·새 정확도와 변화
  (오르면 `+`)를 stdout에 출력한다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = ROOT / "evals" / "scenarios"
TOOLS = [
    "excel_to_report_ppt",
    "doc_to_task_table",
    "apply_template",
    "verify_numbers",
    "save_recipe",
    "run_recipe",
]
DATE = "2026-01-02"


def _load() -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(SCENARIOS.glob("*.json"))]


def _eval(
    tmp: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "docmill.cli", "eval", *args],
        env={**os.environ, "DOCMILL_HOME": str(tmp), **(env or {})},
        cwd=str(tmp),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _run_fake(
    tmp: Path,
    responses: dict[str, Any],
    date: str = DATE,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    tmp.mkdir(parents=True, exist_ok=True)
    resp = tmp / "responses.json"
    resp.write_text(json.dumps(responses, ensure_ascii=False), encoding="utf-8")
    out = tmp / "reports"
    proc = _eval(
        tmp,
        "--model",
        "fake",
        "--responses",
        str(resp),
        "--scenarios",
        str(SCENARIOS),
        "--out-dir",
        str(out),
        "--date",
        date,
        env=env,
    )
    return proc, out


def _answers(wrong_tool: set[str], wrong_args: set[str]) -> dict[str, Any]:
    """기본은 정답. wrong_tool은 엉뚱한 도구, wrong_args는 도구는 맞고 인자만 틀린 응답."""
    resp: dict[str, Any] = {}
    for s in _load():
        exp = s["expected"]
        if s["id"] in wrong_tool:
            other = "verify_numbers" if exp["tool"] != "verify_numbers" else "save_recipe"
            resp[s["id"]] = {"tool": other, "args": {}}
        elif s["id"] in wrong_args:
            resp[s["id"]] = {"tool": exp["tool"], "args": {"__wrong__": 1}}
        else:
            resp[s["id"]] = {"tool": exp["tool"], "args": exp.get("args", {})}
    return resp


@pytest.mark.fr("FR-23")
def test_scenario_set_covers_all_tools_and_no_tool_cases() -> None:
    scenarios = _load()
    assert len(scenarios) >= 30
    counts = {t: 0 for t in TOOLS}
    none_count = 0
    for s in scenarios:
        tool = s["expected"]["tool"]
        if tool is None:
            none_count += 1
        else:
            counts[tool] += 1
    assert all(n >= 3 for n in counts.values()), counts
    assert none_count >= 3


@pytest.mark.fr("FR-23")
def test_fake_model_scores_match_expected(tmp_path: Path) -> None:
    scenarios = [s for s in _load() if s["expected"]["tool"]]
    wrong_tool = {scenarios[0]["id"]}
    wrong_args = {scenarios[1]["id"]}
    proc, out = _run_fake(tmp_path, _answers(wrong_tool, wrong_args))
    assert proc.returncode == 0, proc.stderr
    report = json.loads((out / f"{DATE}.json").read_text(encoding="utf-8"))
    for s in _load():
        row = report["scenarios"][s["id"]]
        assert row["tool_match"] is (s["id"] not in wrong_tool)
        assert row["args_match"] is (s["id"] not in wrong_tool | wrong_args)
    for tool in TOOLS:
        mine = [s for s in _load() if s["expected"]["tool"] == tool]
        ok_tool = sum(1 for s in mine if s["id"] not in wrong_tool)
        ok_args = sum(1 for s in mine if s["id"] not in wrong_tool | wrong_args)
        assert report["tools"][tool]["tool_rate"] == pytest.approx(ok_tool / len(mine))
        assert report["tools"][tool]["args_rate"] == pytest.approx(ok_args / len(mine))


@pytest.mark.fr("FR-23")
def test_compare_prints_per_tool_accuracy_change(tmp_path: Path) -> None:
    scenarios = [s for s in _load() if s["expected"]["tool"]]
    worse_id = scenarios[0]["id"]
    improved_tool = scenarios[0]["expected"]["tool"]
    _, out_a = _run_fake(tmp_path / "a", _answers({worse_id}, set()), "2026-01-01")
    _, out_b = _run_fake(tmp_path / "b", _answers(set(), set()), "2026-01-02")
    proc = _eval(
        tmp_path,
        "--compare",
        str(out_a / "2026-01-01.json"),
        str(out_b / "2026-01-02.json"),
    )
    assert proc.returncode == 0, proc.stderr
    for tool in TOOLS:
        assert tool in proc.stdout
    line = next(ln for ln in proc.stdout.splitlines() if improved_tool in ln)
    assert "+" in line, "정확도가 오른 도구에 변화가 표시되어야 한다"


@pytest.mark.fr("FR-23")
def test_works_without_api_key_and_ignores_it(tmp_path: Path) -> None:
    answers = _answers(set(), set())
    removed = {"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""}
    proc_a, out_a = _run_fake(tmp_path / "a", answers, env=removed)
    proc_b, out_b = _run_fake(
        tmp_path / "b", answers, env={**removed, "ANTHROPIC_API_KEY": "sk-ant-test-not-real"}
    )
    assert proc_a.returncode == 0, proc_a.stderr
    assert proc_b.returncode == 0, proc_b.stderr
    assert "sk-ant-test-not-real" not in proc_b.stdout + proc_b.stderr
    assert (out_a / f"{DATE}.json").read_bytes() == (out_b / f"{DATE}.json").read_bytes()


@pytest.mark.fr("FR-23")
def test_report_uses_given_date_and_is_byte_identical(tmp_path: Path) -> None:
    answers = _answers(set(), set())
    _, out_a = _run_fake(tmp_path / "a", answers, "2031-05-06")
    _, out_b = _run_fake(tmp_path / "b", answers, "2031-05-06")
    for ext in ("md", "json"):
        pa = out_a / f"2031-05-06.{ext}"
        pb = out_b / f"2031-05-06.{ext}"
        assert pa.exists()
        assert pa.read_bytes() == pb.read_bytes()
