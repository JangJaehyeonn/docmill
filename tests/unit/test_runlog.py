from pathlib import Path

import pytest

from docmill import runlog


def test_record_and_get_run(tmp_path: Path) -> None:
    runlog.record_run(
        run_id="r1",
        tool="t",
        args_summary="a=1",
        result="ok",
        status="ok",
        duration_ms=3,
        ai_used=True,
        home=tmp_path,
    )
    rec = runlog.get_run("r1", home=tmp_path)
    assert rec is not None and rec["tool"] == "t" and rec["ai_used"] is True
    assert runlog.get_run("nope", home=tmp_path) is None


def test_summarize_redacts_and_clips() -> None:
    out = runlog.summarize_args(
        {"api_key": "abc", "note": "token=xyz", "data": "X" * 5000, "rows": [1, 2], "b": b"12"}
    )
    assert "abc" not in out and "xyz" not in out and "X" * 300 not in out
    assert "rows=<list 2개>" in out and "<bytes 2>" in out


def test_logged_records_success_and_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))

    def ok(x: int) -> str:
        return f"v{x}"

    def bad() -> None:
        raise ValueError("boom")

    assert runlog.logged(ok)(2) == "v2"
    with pytest.raises(ValueError):
        runlog.logged(bad)()
    con = runlog._connect(tmp_path)
    rows = {r["tool"]: r for r in con.execute("SELECT * FROM tool_runs")}
    con.close()
    assert rows["ok"]["status"] == "ok" and rows["ok"]["args_summary"] == "x=2"
    assert rows["bad"]["status"] == "error" and "boom" in rows["bad"]["result"]
