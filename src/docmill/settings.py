"""작업 폴더의 사용자 설정 파일(`.docmill/settings.json`) 읽기 (FR-45).

부서·이름·직함·결재 라인·기본 양식·문체를 담는다. studio 설정 화면이 쓰고,
preflight 가 문서의 직함을 설정과 대조할 때 읽는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class UserSettings:
    """사용자 설정. 비어 있는 값은 빈 문자열·빈 목록이다."""

    department: str = ""
    name: str = ""
    title: str = ""
    approval_line: list[str] = field(default_factory=list)
    default_template: str = ""
    tone: str = ""


def settings_path(workspace: Path) -> Path:
    """작업 폴더의 설정 파일 경로."""
    return workspace / ".docmill" / "settings.json"


def _text(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def load_settings(workspace: Path) -> UserSettings | None:
    """설정 파일을 읽는다. 없거나 읽을 수 없으면 None."""
    try:
        data = json.loads(settings_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    line = data.get("approval_line")
    steps = (
        [s.strip() for s in line if isinstance(s, str) and s.strip()]
        if isinstance(line, list)
        else []
    )
    return UserSettings(
        department=_text(data, "department"),
        name=_text(data, "name"),
        title=_text(data, "title"),
        approval_line=steps,
        default_template=_text(data, "default_template"),
        tone=_text(data, "tone"),
    )
