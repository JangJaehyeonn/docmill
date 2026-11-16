"""FR-24 원격 모드 배포 자동화 인수 테스트 (초안).

필요한 샘플: 없음. Docker 테스트는 docker가 없으면 건너뛴다.
가정(구현 방법이 정해지면 맞춰 고친다):
- `docmill mcp --http --port N`이 `/mcp`를 제공한다.
- 인증 토큰: 환경 변수 `DOCMILL_HTTP_TOKEN`. 토큰이 없으면 127.0.0.1에만 바인딩한다.
- `/healthz` 응답: `{"engines": {"gridcraft": bool, "deckcraft": bool, "wordcraft": bool}}`
  (준비됨 = true). 하나라도 false면 503. 엔진을 못 찾게 하려면
  `DOCMILL_<이름대문자>_PATH=/nonexistent/engine` (기존 엔진 경로 변수) 로 바꾼다.
- 저장소 루트의 `Dockerfile`이 이미지를 만들고, 컨테이너는 포트 8000에서 HTTP 모드를 띄운다.
- `deploy/deploy.sh --image <새 이미지> --previous <이전 이미지> --timeout <초>` 는
  PATH의 `docker`와 `curl`을 쓴다. 컨테이너는 `docker run [옵션] <이미지> [명령]`으로 띄운다.
  옵션은 `-d`·`--rm` 같은 단독 플래그이거나 `--name=x`·`--publish=...`처럼 `=`로 값을 붙여 쓴다.
  그래서 `run` 뒤 첫 번째 `-`로 시작하지 않는 인자가 이미지다. 헬스 체크는 `curl`로 하고,
  실패하면 `docker run ... <이전 이미지>`로 되돌린다.
  테스트는 가짜 `docker`·`curl` 스크립트를 PATH 앞에 두고 호출 기록(`$FAKE_LOG`)을 확인한다.
  가짜 curl은 `FAKE_HEALTH=fail`이면 실패한다.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[3]
TOKEN = "test-token-123"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _spawn(home: Path, env: dict[str, str], port: int) -> subprocess.Popen[bytes]:
    base = {k: v for k, v in os.environ.items() if k != "DOCMILL_HTTP_TOKEN"}
    return subprocess.Popen(
        [sys.executable, "-m", "docmill.cli", "mcp", "--http", "--port", str(port)],
        env={**base, "DOCMILL_HOME": str(home), **env},
        cwd=str(home),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_open(port: int, proc: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert proc.poll() is None, "HTTP 서버가 바로 종료됐다"
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise AssertionError("HTTP 서버가 열리지 않았다")


@contextmanager
def _server(home: Path, env: dict[str, str], port: int) -> Iterator[subprocess.Popen[bytes]]:
    proc = _spawn(home, env, port)
    try:
        _wait_open(port, proc)
        yield proc
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _status(url: str, token: str | None = None, method: str = "GET") -> int:
    req = urllib.request.Request(url, method=method, data=b"{}" if method == "POST" else None)
    req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as err:
        return int(err.code)


@pytest.mark.fr("FR-24")
def test_bearer_token_required_when_configured(tmp_path: Path) -> None:
    port = _free_port()
    with _server(tmp_path, {"DOCMILL_HTTP_TOKEN": TOKEN}, port):
        url = f"http://127.0.0.1:{port}/mcp"
        assert _status(url, None, "POST") == 401
        assert _status(url, "wrong-token", "POST") == 401
        # 올바른 토큰이면 실제 MCP 초기화와 도구 조회까지 된다.
        names = asyncio.run(asyncio.wait_for(_list_tools(port, TOKEN), 30))
        assert "excel_to_report_ppt" in names


@pytest.mark.fr("FR-24")
def test_without_token_binds_loopback_only(tmp_path: Path) -> None:
    port = _free_port()
    with _server(tmp_path, {}, port):
        out = subprocess.run(
            ["ss", "-ltn", f"sport = :{port}"], capture_output=True, text=True, timeout=10
        ).stdout
        assert f"127.0.0.1:{port}" in out
        assert f"0.0.0.0:{port}" not in out
        assert f"*:{port}" not in out


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as err:
        return int(err.code), err.read()


@pytest.mark.fr("FR-24")
def test_healthz_reports_three_engines(tmp_path: Path) -> None:
    port = _free_port()
    with _server(tmp_path, {}, port):
        status, body = _get(f"http://127.0.0.1:{port}/healthz")
        engines = json.loads(body)["engines"]
        assert set(engines) == {"gridcraft", "deckcraft", "wordcraft"}
        assert status == (200 if all(engines.values()) else 503)


@pytest.mark.fr("FR-24")
def test_healthz_503_when_an_engine_is_not_ready(tmp_path: Path) -> None:
    port = _free_port()
    with _server(tmp_path, {"DOCMILL_GRIDCRAFT_PATH": "/nonexistent/engine"}, port):
        status, body = _get(f"http://127.0.0.1:{port}/healthz")
        assert status == 503
        assert json.loads(body)["engines"]["gridcraft"] is False


async def _list_tools(port: int, token: str) -> set[str]:
    import httpx

    url = f"http://127.0.0.1:{port}/mcp"
    async with (
        httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as client,
        streamable_http_client(url, http_client=client) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        return {t.name for t in (await session.list_tools()).tools}


def _docker_health(port: int) -> int:
    try:
        return _status(f"http://127.0.0.1:{port}/healthz", TOKEN)
    except OSError:
        return 0


@pytest.mark.fr("FR-24")
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker가 없다")
def test_docker_image_serves_http_mode_with_engines() -> None:
    tag = "docmill-acceptance:test"
    build = subprocess.run(
        ["docker", "build", "-t", tag, str(ROOT)], capture_output=True, text=True, timeout=900
    )
    assert build.returncode == 0, build.stderr
    port = _free_port()
    run_cmd = [
        "docker",
        "run",
        "-d",
        "-p",
        f"127.0.0.1:{port}:8000",
        "-e",
        f"DOCMILL_HTTP_TOKEN={TOKEN}",
        tag,
    ]
    cid = subprocess.run(run_cmd, capture_output=True, text=True, timeout=60).stdout.strip()
    try:
        assert cid
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and _docker_health(port) != 200:
            time.sleep(1)
        assert _docker_health(port) == 200
        names = asyncio.run(asyncio.wait_for(_list_tools(port, TOKEN), 30))
        assert "excel_to_report_ppt" in names
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=60)


def _fake_bin(directory: Path) -> Path:
    directory.mkdir(parents=True)
    (directory / "docker").write_text('#!/bin/sh\necho "docker $*" >> "$FAKE_LOG"\nexit 0\n')
    (directory / "curl").write_text(
        '#!/bin/sh\necho "curl $*" >> "$FAKE_LOG"\n'
        'if [ "$FAKE_HEALTH" = "fail" ]; then exit 22; fi\nexit 0\n'
    )
    for f in directory.iterdir():
        f.chmod(0o755)
    return directory


def _deploy(tmp: Path, health: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = _fake_bin(tmp / f"bin_{health}")
    log = tmp / f"log_{health}.txt"
    log.write_text("")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_LOG": str(log),
        "FAKE_HEALTH": health,
    }
    proc = subprocess.run(
        [
            "bash",
            str(ROOT / "deploy" / "deploy.sh"),
            "--image",
            "docmill:new",
            "--previous",
            "docmill:old",
            "--timeout",
            "3",
        ],
        env=env,
        cwd=str(tmp),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc, log.read_text().splitlines()


def _run_image(call: str) -> str | None:
    """`docker run [옵션] IMAGE [명령]`에서 IMAGE를 꺼낸다. docker run이 아니면 None."""
    parts = call.split()
    if parts[:2] != ["docker", "run"]:
        return None
    return next((p for p in parts[2:] if not p.startswith("-")), None)


def _run_index(calls: list[str], image: str) -> list[int]:
    """IMAGE 자리가 image인 `docker run` 호출의 위치 목록."""
    return [i for i, c in enumerate(calls) if _run_image(c) == image]


@pytest.mark.fr("FR-24")
def test_deploy_rolls_back_when_health_check_fails(tmp_path: Path) -> None:
    proc, calls = _deploy(tmp_path, "fail")
    assert proc.returncode != 0
    new_runs = _run_index(calls, "docmill:new")
    old_runs = _run_index(calls, "docmill:old")
    assert new_runs, f"새 이미지를 실행하지 않았다: {calls}"
    checks = [i for i, c in enumerate(calls) if c.startswith("curl ") and i > new_runs[0]]
    assert checks, f"새 이미지 실행 뒤 헬스 체크가 없다: {calls}"
    assert old_runs and old_runs[-1] > checks[0], (
        f"헬스 체크 실패 뒤 이전 이미지를 다시 실행해야 한다: {calls}"
    )


@pytest.mark.fr("FR-24")
def test_deploy_keeps_new_image_when_healthy(tmp_path: Path) -> None:
    proc, calls = _deploy(tmp_path, "ok")
    assert proc.returncode == 0, proc.stderr
    assert _run_index(calls, "docmill:new"), f"새 이미지를 실행하지 않았다: {calls}"
    assert not _run_index(calls, "docmill:old"), f"정상인데 이전 이미지를 실행했다: {calls}"
