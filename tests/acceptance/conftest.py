"""인수 테스트 실행 범위를 정한다.

- 모든 인수 테스트는 `@pytest.mark.fr("FR-xx")`로 요구사항을 표시해야 한다.
  표시가 없으면 수집 단계에서 실패한다.
- `DOCMILL_ACTIVE_FRS`(쉼표 구분)에 든 요구사항의 테스트만 실행한다.
  나머지는 "구현 전"으로 건너뛴다.
- CI는 이 값을 '완료된 요구사항 + 이 PR의 요구사항'으로 채우고, 계산에 실패하면 CI 자체가 실패한다.
"""

from __future__ import annotations

import os
from pathlib import Path

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
