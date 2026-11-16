"""FR-04 엔진 설치 명령 인수 테스트 (초안).

필요한 샘플: 없음 (네트워크로 엔진을 내려받는다).
가정(구현 방법이 정해지면 맞춰 고친다):
- `DOCMILL_HOME`이 엔진 설치 위치의 기준 폴더다.
- 출력에 엔진 이름(GridCraft·DeckCraft·WordCraft)과 그 엔진의 고정 버전이 같은 줄에 나온다.
- 다운로드는 표준 프록시 환경 변수(HTTPS_PROXY·HTTP_PROXY)를 따른다.
  테스트는 요청을 기록하는 로컬 프록시를 띄워 두 실행 모두 그 프록시를 거치게 한다.
  첫 실행은 프록시를 거쳐 실제로 내려받아야 하고(프록시 준수 확인),
  두 번째 실행은 프록시에 요청이 하나도 없어야 한다(재다운로드 없음 확인).
"""

from __future__ import annotations

import os
import re
import select
import socket
import socketserver
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

# 고정 엔진 버전 (docs/phase0.md, .github/actions/setup-engines와 같다). 엔진을 올리면 함께 바꾼다.
ENGINES = {"GridCraft": "0.9.0", "DeckCraft": "0.9.0", "WordCraft": "0.8.0"}


class _RecordingProxy(socketserver.ThreadingTCPServer):
    """요청 줄을 기록하고 CONNECT·평문 요청을 그대로 중계하는 최소 HTTP 프록시."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _ProxyHandler)
        self.requests: list[str] = []
        self.lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _ProxyHandler(socketserver.StreamRequestHandler):
    server: _RecordingProxy

    def handle(self) -> None:
        line = self.rfile.readline(65536).decode("latin-1").strip()
        if not line:
            return
        with self.server.lock:
            self.server.requests.append(line)
        headers = b""
        while True:
            h = self.rfile.readline(65536)
            if h in (b"\r\n", b"\n", b""):
                break
            headers += h
        method, target, _ = line.split(" ", 2)
        if method == "CONNECT":
            host, _, port = target.partition(":")
            upstream = socket.create_connection((host, int(port or 443)), timeout=60)
            self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            self._pipe(upstream)
        else:
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")

    def _pipe(self, upstream: socket.socket) -> None:
        conns = [self.connection, upstream]
        try:
            while True:
                ready, _, _ = select.select(conns, [], [], 60)
                if not ready:
                    return
                for s in ready:
                    data = s.recv(65536)
                    if not data:
                        return
                    (upstream if s is self.connection else self.connection).sendall(data)
        finally:
            upstream.close()


@pytest.fixture
def proxy() -> Iterator[_RecordingProxy]:
    server = _RecordingProxy()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _proxy_env(url: str) -> dict[str, str]:
    return {
        "HTTPS_PROXY": url,
        "HTTP_PROXY": url,
        "https_proxy": url,
        "http_proxy": url,
        "NO_PROXY": "",
        "no_proxy": "",
    }


def _setup(home: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "docmill.cli", "setup"],
        env={**os.environ, "DOCMILL_HOME": str(home), **(extra_env or {})},
        cwd=str(home),
        capture_output=True,
        text=True,
        timeout=600,
    )


def _assert_versions(stdout: str) -> None:
    lines = stdout.splitlines()
    for engine, version in ENGINES.items():
        pattern = rf"(?<![\d.]){re.escape(version)}(?![\d.])"
        found = any(engine in ln and re.search(pattern, ln) for ln in lines)
        assert found, f"{engine} {version}이 출력되지 않았다: {stdout}"


@pytest.mark.fr("FR-04")
def test_setup_prints_version_of_each_engine(tmp_path: Path) -> None:
    proc = _setup(tmp_path)
    assert proc.returncode == 0, proc.stderr
    _assert_versions(proc.stdout)


@pytest.mark.fr("FR-04")
def test_setup_twice_does_not_download_again(tmp_path: Path, proxy: _RecordingProxy) -> None:
    env = _proxy_env(proxy.url)
    first = _setup(tmp_path, env)
    assert first.returncode == 0, first.stderr
    # 첫 실행이 프록시를 거쳤어야, 두 번째 실행의 "요청 없음"이 의미가 있다.
    assert proxy.requests, "첫 설치가 프록시 환경 변수를 따르지 않았다"
    proxy.requests.clear()

    second = _setup(tmp_path, env)
    assert second.returncode == 0, second.stderr
    assert proxy.requests == [], f"이미 설치된 엔진을 다시 내려받으려 했다: {proxy.requests}"
    _assert_versions(second.stdout)
