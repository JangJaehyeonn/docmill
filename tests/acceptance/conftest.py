"""인수 테스트 실행 범위를 정한다.

각 테스트는 `@pytest.mark.fr("FR-xx")`로 요구사항을 표시한다.
`DOCMILL_ACTIVE_FRS` 환경 변수(쉼표 구분)에 들어 있는 요구사항의 테스트만 실행하고,
나머지는 "아직 구현 전"으로 건너뛴다. CI는 이 값을 '완료된 요구사항 + 이 PR의 요구사항'으로 채운다.
"""

from __future__ import annotations

import os

import pytest


def _active_frs() -> set[str]:
    raw = os.environ.get("DOCMILL_ACTIVE_FRS", "")
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    active = _active_frs()
    for item in items:
        marker = item.get_closest_marker("fr")
        if marker is None:
            continue
        fr = str(marker.args[0]).upper()
        if fr not in active:
            item.add_marker(pytest.mark.skip(reason=f"{fr}: 아직 구현 전인 요구사항"))
