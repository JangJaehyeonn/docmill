"""FR-02 문서 엔진 연동 인수 테스트 (초안).

필요한 샘플 (tests/fixtures/): sales_q3.xlsx, company_intro.pptx, meeting_1111.docx
가정(구현에 맞춰 고친다):
- 어댑터: docmill.engines.gridcraft.GridCraftTool, deckcraft.DeckCraftTool,
  wordcraft.WordCraftTool. 생성자는 `(workdir: Path, timeout: float = 60)`이며 workdir은 필수다.
- 엔진 실행 파일 경로는 환경 변수 DOCMILL_GRIDCRAFT_PATH / DOCMILL_DECKCRAFT_PATH /
  DOCMILL_WORDCRAFT_PATH로 지정한다. 어댑터는 컨텍스트 매니저이고 `list_tools()`를 가진다.
- 기본 시간 제한은 `docmill.engines.DEFAULT_TIMEOUT_SECONDS`(60).
- 실패는 예외가 아니라 `EngineResult(ok=False, message=...)`로 돌려준다.
- 가짜 엔진 테스트는 POSIX 전용이다 (실행 가능한 스크립트 사용).
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

# 엔진 이름 -> (어댑터 클래스, 경로 환경 변수, 실행 파일 이름, 샘플 파일)
ENGINES = {
    "gridcraft": ("GridCraftTool", "DOCMILL_GRIDCRAFT_PATH", "gridcraft-cli", "sales_q3.xlsx"),
    "deckcraft": ("DeckCraftTool", "DOCMILL_DECKCRAFT_PATH", "deckcraft-cli", "company_intro.pptx"),
    "wordcraft": ("WordCraftTool", "DOCMILL_WORDCRAFT_PATH", "wordcraft-cli", "meeting_1111.docx"),
}
ROOT_FLAG_ENGINES = ["deckcraft", "wordcraft"]

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="가짜 엔진은 POSIX 전용")


def _adapter(engine: str, workdir: Path | None, **kwargs: Any) -> Any:
    module = importlib.import_module(f"docmill.engines.{engine}")
    return getattr(module, ENGINES[engine][0])(workdir=workdir, **kwargs)


def _fake_engine(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake-engine"
    script.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.mark.fr("FR-02")
@pytest.mark.engine
@pytest.mark.parametrize("engine", list(ENGINES))
def test_client_lists_tools_and_calls_one(
    engine: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, env_name, binary, sample = ENGINES[engine]
    exe = shutil.which(binary)
    if exe is None:
        pytest.fail(f"{binary} 실행 파일이 없다 (CI는 setup-engines로 설치한다)")
    monkeypatch.setenv(env_name, exe)
    shutil.copy(FIXTURES / sample, tmp_path / sample)
    with _adapter(engine, tmp_path) as tool:
        assert tool.list_tools(), "엔진 도구 목록이 비어 있다"
        result = tool.open(tmp_path / sample)
        assert result.ok, result.message


@pytest.mark.fr("FR-02")
@pytest.mark.parametrize("engine", list(ENGINES))
def test_missing_engine_reports_which_engine(
    engine: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, env_name, _, sample = ENGINES[engine]
    monkeypatch.setenv(env_name, str(tmp_path / "does-not-exist"))
    with _adapter(engine, tmp_path) as tool:
        result = tool.open(tmp_path / sample)  # 예외 없이 결과로 돌아와야 한다
    assert not result.ok
    assert engine in result.message.lower()


@pytest.mark.fr("FR-02")
@posix_only
def test_engine_process_is_killed_after_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docmill.engines import DEFAULT_TIMEOUT_SECONDS

    assert DEFAULT_TIMEOUT_SECONDS == 60
    pid_file = tmp_path / "pid"
    fake = _fake_engine(
        tmp_path,
        f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(300)",
    )
    monkeypatch.setenv("DOCMILL_GRIDCRAFT_PATH", str(fake))
    workdir = tmp_path / "work"
    workdir.mkdir()
    started = time.monotonic()
    with _adapter("gridcraft", workdir, timeout=1) as tool:
        result = tool.open(workdir / "a.xlsx")
    assert time.monotonic() - started < 30
    assert not result.ok
    assert "시간" in result.message or "timeout" in result.message.lower()
    pid = int(pid_file.read_text())
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.fr("FR-02")
@posix_only
@pytest.mark.parametrize("engine", ROOT_FLAG_ENGINES)
def test_engine_launched_with_work_folder_roots(
    engine: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv_file = tmp_path / "argv.json"
    fake = _fake_engine(
        tmp_path,
        f"import json, sys\njson.dump(sys.argv[1:], open({str(argv_file)!r}, 'w'))",
    )
    monkeypatch.setenv(ENGINES[engine][1], str(fake))
    workdir = tmp_path / "work"
    workdir.mkdir()
    with _adapter(engine, workdir, timeout=5) as tool:
        tool.open(workdir / "a")
    argv = json.loads(argv_file.read_text())
    for flag in ("--automation-read-root", "--automation-write-root"):
        assert flag in argv, f"{flag} 없이 엔진이 실행됐다"
        assert Path(argv[argv.index(flag) + 1]).resolve() == workdir.resolve()


@pytest.mark.fr("FR-02")
@pytest.mark.parametrize("engine", ROOT_FLAG_ENGINES)
def test_engine_not_started_without_work_folder(engine: str) -> None:
    with pytest.raises((TypeError, ValueError)), _adapter(engine, None) as tool:
        tool.open(Path("a"))


@pytest.mark.fr("FR-02")
@posix_only
@pytest.mark.parametrize("engine", list(ENGINES))
@pytest.mark.parametrize("kind", ["dotdot", "absolute", "symlink"])
def test_paths_outside_work_folder_rejected_before_engine_call(
    engine: str, kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = tmp_path / "engine-called"
    fake = _fake_engine(tmp_path, f"open({str(called)!r}, 'w').write('x')")
    monkeypatch.setenv(ENGINES[engine][1], str(fake))
    workdir = tmp_path / "work"
    workdir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    ext = Path(ENGINES[engine][3]).suffix
    if kind == "dotdot":
        target = workdir / ".." / "outside" / f"secret{ext}"
    elif kind == "absolute":
        target = outside / f"secret{ext}"
    else:
        (workdir / "link").symlink_to(outside, target_is_directory=True)
        target = workdir / "link" / f"secret{ext}"
    with _adapter(engine, workdir, timeout=5) as tool:
        results = [tool.open(target), tool.save(target), tool.render(target, workdir)]
    assert all(not r.ok and r.message for r in results)
    assert not called.exists(), "작업 폴더 밖 경로인데 엔진이 호출됐다"


@pytest.mark.fr("FR-02")
@posix_only
@pytest.mark.parametrize("engine", list(ENGINES))
@pytest.mark.parametrize("kind", ["dotdot", "absolute", "symlink"])
def test_render_output_outside_work_folder_rejected_before_engine_call(
    engine: str, kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = tmp_path / "engine-called"
    fake = _fake_engine(tmp_path, f"open({str(called)!r}, 'w').write('x')")
    monkeypatch.setenv(ENGINES[engine][1], str(fake))
    workdir = tmp_path / "work"
    workdir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    inside_doc = workdir / f"doc{Path(ENGINES[engine][3]).suffix}"
    if kind == "dotdot":
        out_dir = workdir / ".." / "outside"
    elif kind == "absolute":
        out_dir = outside
    else:
        (workdir / "out-link").symlink_to(outside, target_is_directory=True)
        out_dir = workdir / "out-link"
    with _adapter(engine, workdir, timeout=5) as tool:
        result = tool.render(inside_doc, out_dir)
    assert not result.ok and result.message
    assert not called.exists(), "출력 폴더가 작업 폴더 밖인데 엔진이 호출됐다"
    assert not any(outside.iterdir()), "작업 폴더 밖에 파일이 생겼다"
