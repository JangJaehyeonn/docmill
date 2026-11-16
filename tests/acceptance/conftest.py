"""인수 테스트 실행 범위를 정한다.

- 모든 인수 테스트는 `@pytest.mark.fr("FR-xx")`로 요구사항을 표시해야 한다.
  표시가 없으면 수집 단계에서 실패한다.
- `DOCMILL_ACTIVE_FRS`(쉼표 구분)에 든 요구사항의 테스트만 실행한다.
  나머지는 "구현 전"으로 건너뛴다.
- CI는 이 값을 '완료된 요구사항 + 이 PR의 요구사항'으로 채우고, 계산에 실패하면 CI 자체가 실패한다.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

_ACCEPTANCE_DIR = Path(__file__).parent.resolve()


def _active_frs() -> set[str]:
    raw = os.environ.get("DOCMILL_ACTIVE_FRS", "")
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def _is_acceptance(item: pytest.Item) -> bool:
    return _ACCEPTANCE_DIR in Path(str(item.path)).resolve().parents


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # This hook sees every collected item, so limit it to tests under this folder.
    items = [item for item in items if _is_acceptance(item)]
    missing = [item.nodeid for item in items if item.get_closest_marker("fr") is None]
    if missing:
        raise pytest.UsageError(
            "인수 테스트에는 @pytest.mark.fr('FR-xx') 표시가 필요합니다: " + ", ".join(missing)
        )
    active = _active_frs()
    for item in items:
        marker = item.get_closest_marker("fr")
        assert marker is not None
        fr = str(marker.args[0]).upper()
        if fr not in active:
            item.add_marker(pytest.mark.skip(reason=f"{fr}: 아직 구현 전인 요구사항"))


# ── studio(앱) 인수 테스트용 ─────────────────────────────────────────────
# 계약 (CLAUDE.md "studio" 절과 같다):
# - `npm --prefix studio run build` 결과로 `studio/out/server.js`가 생긴다.
# - `node studio/out/server.js --port <포트>`는 앱 화면을 HTTP로 내보낸다.
#   준비되면 stdout에 `READY <url>` 한 줄을 쓴다.
# - 환경 변수:
#   DOCMILL_STUDIO_WORKSPACE(열고 저장하는 작업 폴더), RHWP_STUDIO_DIST(rhwp 편집기 빌드),
#   DOCMILL_ONLYOFFICE_URL(문서 서버), DOCMILL_STUDIO_AGENT_CMD(채팅 에이전트 명령, 없으면 claude).


_REPO = _ACCEPTANCE_DIR.parent.parent
_SERVER = _REPO / "studio" / "out" / "server.js"


def _free_port() -> int:
    # ONLYOFFICE가 host 네트워크에서 80·8000을 쓰므로 OS가 고른 빈 포트를 쓴다.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def start_studio(tmp_path: Path) -> Iterator[Callable[..., str]]:
    """studio 서버를 띄우고 URL을 돌려주는 함수를 준다. 테스트가 끝나면 서버를 끈다.

    사용: `url = start_studio(workspace=dir, env={"DOCMILL_STUDIO_AGENT_CMD": "..."})`
    """
    procs: list[subprocess.Popen[str]] = []

    def _start(workspace: Path | None = None, env: dict[str, str] | None = None) -> str:
        if not _SERVER.exists():
            pytest.fail("studio가 빌드되지 않았습니다: npm --prefix studio run build")
        ws = workspace or (tmp_path / "workspace")
        ws.mkdir(parents=True, exist_ok=True)
        full_env = {**os.environ, "DOCMILL_STUDIO_WORKSPACE": str(ws), **(env or {})}
        proc = subprocess.Popen(
            ["node", str(_SERVER), "--port", str(_free_port())],
            cwd=_REPO,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
            encoding="utf-8",
        )
        procs.append(proc)
        deadline = time.monotonic() + 60
        assert proc.stdout is not None
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if line.startswith("READY "):
                return line.split(" ", 1)[1].strip()
            if not line and proc.poll() is not None:
                break
        pytest.fail("studio 서버가 60초 안에 READY를 출력하지 않았습니다.")

    yield _start
    for proc in procs:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def page() -> Iterator[Any]:
    """Chromium 페이지 하나 (Playwright 동기 API). 화면 크기는 1440×900."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR")
        try:
            yield context.new_page()
        finally:
            context.close()
            browser.close()
