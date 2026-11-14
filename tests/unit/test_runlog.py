import asyncio
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


def test_summarize_redacts_json_quoted_secret() -> None:
    out = runlog.summarize_args({"payload": '{"api_key": "abc123 def", "n": 1}'})
    assert "abc123" not in out and "def" not in out


def test_logged_async_records_cancel_and_start_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))

    async def slow() -> None:
        await asyncio.sleep(10)

    async def ok() -> str:
        return "fine"

    async def main() -> None:
        assert await runlog.logged(ok)() == "fine"
        task = asyncio.create_task(runlog.logged(slow)())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    con = runlog._connect(tmp_path)
    rows = {r["tool"]: r for r in con.execute("SELECT * FROM tool_runs")}
    con.close()
    assert rows["ok"]["status"] == "ok"
    assert rows["slow"]["status"] == "cancelled"


def test_logged_async_keeps_record_when_cancelled_while_executor_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실행기가 가득 찬 상태에서 기록 대기 중 취소돼도 기록은 남아야 한다."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    release = threading.Event()

    async def quick() -> str:
        return "fine"

    async def main() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        blocker = loop.run_in_executor(None, release.wait)
        task = asyncio.create_task(runlog.logged(quick)())
        await asyncio.sleep(0.05)  # task is now waiting for the busy executor
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await blocker

    asyncio.run(main())
    con = runlog._connect(tmp_path)
    rows = list(con.execute("SELECT tool, status FROM tool_runs"))
    con.close()
    assert [(r["tool"], r["status"]) for r in rows] == [("quick", "ok")]


def test_record_run_keeps_given_started_at(tmp_path: Path) -> None:
    runlog.record_run(
        run_id="r2",
        tool="t",
        args_summary="",
        result="",
        status="ok",
        duration_ms=1,
        ai_used=False,
        started_at="2020-01-01T00:00:00+00:00",
        home=tmp_path,
    )
    rec = runlog.get_run("r2", home=tmp_path)
    assert rec is not None and rec["started_at"] == "2020-01-01T00:00:00+00:00"


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
