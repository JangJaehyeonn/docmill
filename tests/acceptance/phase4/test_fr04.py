"""FR-04 엔진 설치 명령 인수 테스트 (초안).

필요한 샘플: 없음 (네트워크로 엔진을 내려받는다).
가정(구현 방법이 정해지면 맞춰 고친다):
- `DOCMILL_HOME`이 엔진 설치 위치의 기준 폴더다.
- 출력에 엔진 이름(GridCraft·DeckCraft·WordCraft)과 그 엔진의 고정 버전이 같은 줄에 나온다.
- 다운로드는 표준 프록시 환경 변수(HTTPS_PROXY·HTTP_PROXY)를 따른다.
  두 번째 실행은 프록시를 닫힌 포트로 막아 두고 실행해, 다운로드를 시도하면 실패하게 만든다.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# 고정 엔진 버전 (docs/phase0.md, .github/actions/setup-engines와 같다). 엔진을 올리면 함께 바꾼다.
ENGINES = {"GridCraft": "0.9.0", "DeckCraft": "0.9.0", "WordCraft": "0.8.0"}
# 아무것도 듣지 않는 포트. 이 프록시를 거치는 요청은 모두 연결 거부로 실패한다.
BLOCKED_PROXY = "http://127.0.0.1:9"
NO_NETWORK = {
    "HTTPS_PROXY": BLOCKED_PROXY,
    "HTTP_PROXY": BLOCKED_PROXY,
    "https_proxy": BLOCKED_PROXY,
    "http_proxy": BLOCKED_PROXY,
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
def test_setup_twice_does_not_download_again(tmp_path: Path) -> None:
    first = _setup(tmp_path)
    assert first.returncode == 0, first.stderr
    # 두 번째 실행은 네트워크를 막는다.
    # 다시 내려받으려 하면 실패하므로, 성공하면 다운로드가 없었던 것이다.
    second = _setup(tmp_path, NO_NETWORK)
    assert second.returncode == 0, f"이미 설치된 엔진을 다시 내려받으려 했다: {second.stderr}"
    _assert_versions(second.stdout)
