"""폴더 감시(FR-50) 단위 테스트."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from docmill import watch as w


def _fake_run(result: str) -> Any:
    return patch.object(w, "run_recipe", lambda recipe, input, out: result)


def test_same_content_processed_once(tmp_path: Path) -> None:
    (tmp_path / "a.xlsx").write_bytes(b"x")
    (tmp_path / "b.xlsx").write_bytes(b"x")
    (tmp_path / "c.txt").write_bytes(b"y")
    sizes: dict[Path, int] = {}
    seen: set[str] = set()
    with _fake_run('{"path": "p"}'):
        args = ("r", tmp_path, tmp_path / "out", sizes, seen)
        assert w.scan_once(*args, home=tmp_path) == 0  # first pass: size not yet stable
        assert w.scan_once(*args, home=tmp_path) == 1
        assert w.scan_once(*args, home=tmp_path) == 0


def test_preflight_failure_is_logged_without_ok(tmp_path: Path) -> None:
    from docmill import runlog

    f = tmp_path / "a.xlsx"
    f.write_bytes(b"x")
    with _fake_run('{"error": "없는 열: 매출", "step": "preflight"}'):
        assert w.process_file("r", f, tmp_path / "out", tmp_path) is False
    [rec] = runlog.list_recipe_runs("r", home=tmp_path)
    assert rec["status"] == "preflight_failed"
    assert "매출" in rec["result"]
