"""FR-04 엔진 설치 명령 인수 테스트 (초안).

필요한 샘플: 없음 (네트워크로 엔진을 내려받는다).
가정(구현 방법이 정해지면 맞춰 고친다):
- `DOCMILL_HOME`이 엔진 설치 위치의 기준 폴더다.
- 출력에 엔진 이름(GridCraft·DeckCraft·WordCraft)과 버전 번호(x.y.z)가 한 줄씩 나온다.
- 이미 설치돼 있으면 출력에 "이미 설치"가 들어가고 설치된 파일이 바뀌지 않는다.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ENGINES = ["GridCraft", "DeckCraft", "WordCraft"]
LOG_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def _setup(home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "docmill.cli", "setup"],
        env={**os.environ, "DOCMILL_HOME": str(home)},
        cwd=str(home),
        capture_output=True,
        text=True,
        timeout=600,
    )


def _snapshot(home: Path) -> dict[str, tuple[int, int]]:
    """설치 파일 상태(크기·수정 시각)를 기록한다. 실행 로그 DB는 제외한다."""
    return {
        str(p.relative_to(home)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(home.rglob("*"))
        if p.is_file() and not p.name.endswith(LOG_DB_SUFFIXES)
    }


@pytest.mark.fr("FR-04")
def test_setup_prints_version_of_each_engine(tmp_path: Path) -> None:
    proc = _setup(tmp_path)
    assert proc.returncode == 0, proc.stderr
    for engine in ENGINES:
        line = next((ln for ln in proc.stdout.splitlines() if engine in ln), "")
        assert re.search(r"\d+\.\d+\.\d+", line), f"{engine} 버전이 출력되지 않았다: {proc.stdout}"


@pytest.mark.fr("FR-04")
def test_setup_twice_does_not_download_again(tmp_path: Path) -> None:
    first = _setup(tmp_path)
    assert first.returncode == 0, first.stderr
    before = _snapshot(tmp_path)
    assert before, "설치된 파일이 없다"
    second = _setup(tmp_path)
    assert second.returncode == 0, second.stderr
    assert "이미 설치" in second.stdout
    assert _snapshot(tmp_path) == before
