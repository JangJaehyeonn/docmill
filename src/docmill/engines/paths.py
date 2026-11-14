"""작업 폴더 경계 검사 (NFR-05).

엔진에 넘기는 모든 경로는 엔진 호출 전에 이 모듈로 검사한다.
엔진 옵션만으로는 막히지 않는 경우가 있어서(Phase 0) docmill이 먼저 거부한다.
"""

from __future__ import annotations

from pathlib import Path


class PathOutsideWorkdirError(ValueError):
    """경로가 작업 폴더 밖을 가리킬 때 발생한다."""


def resolve_inside(workdir: Path, path: Path | str) -> Path:
    """`path`를 실제 경로(`..`·링크 해석 후)로 바꾸고, 작업 폴더 안일 때만 돌려준다.

    상대 경로는 작업 폴더 기준으로 해석한다. 밖이면 `PathOutsideWorkdirError`.
    """
    root = workdir.resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise PathOutsideWorkdirError(f"작업 폴더 밖의 경로는 쓸 수 없다: {path}")
    return resolved
