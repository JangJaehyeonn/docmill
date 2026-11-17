"""`guarded_edit` 업무 도구 (FR-31).

편집 전에 범위 밖 요소(도형·셀·문단, 단위 밖 구조, 나머지 파트)의 지문(C14N 정규화 XML의
SHA-256)을 남긴다. 편집을 적용한 뒤 지문이 달라진 범위 밖 요소는 원본 바이트로 되돌리고,
되돌린 위치를 결과로 돌려준다. 결과 파일은 원본을 뼈대로 범위 안 요소만 바꿔 끼워 만들므로
같은 입력이면 바이트 단위로 같다.
"""

from __future__ import annotations

import json
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from docmill.runlog import current_run_id, get_home, new_run_id, output_stem
from docmill.tools.edits import Editor, Parts, apply_edit
from docmill.tools.ooxml import OoxmlError, Package, fingerprint_part, fingerprint_xml, root_tags
from docmill.tools.scope import KINDS, GuardedPart, ScopeError, layout


class GuardError(Exception):
    """작업 단계 하나가 실패했을 때 발생한다. `step`은 실패한 단계 이름이다."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


@dataclass(frozen=True)
class PartView:
    """파트 하나를 단위로 나눈 모습: 단위 조각(키 순서), 단위 지문, 단위 밖 구조 지문."""

    text: str
    keys: list[str]
    fragments: dict[str, tuple[int, int]]
    unit_prints: dict[str, str]
    skeleton_print: str

    @classmethod
    def of(cls, guarded: GuardedPart, text: str) -> PartView:
        """파트 문자열을 단위로 나누고 지문을 만든다."""
        spans, keys = guarded.units(text)
        opening, closing = root_tags(text)
        fragments = {k: (s.start, s.end) for k, s in zip(keys, spans, strict=True)}
        prints = {
            k: fingerprint_xml(opening + text[a:b] + closing) for k, (a, b) in fragments.items()
        }
        pieces, cursor = [], 0
        for start, end in fragments.values():
            pieces.append(text[cursor:start])
            cursor = end
        pieces.append(text[cursor:])
        return cls(text, keys, fragments, prints, fingerprint_xml("".join(pieces)))

    def fragment(self, key: str) -> str:
        """키에 해당하는 단위의 원문 조각."""
        start, end = self.fragments[key]
        return self.text[start:end]


@dataclass(frozen=True)
class Snapshot:
    """편집 전 지문: 파트별 지문과 단위로 나눈 파트."""

    part_prints: dict[str, str]
    views: dict[str, PartView]


@dataclass
class Outcome:
    """지키기 결과: 내보낼 파트, 되돌린 위치, 범위 안에서 바뀐 위치."""

    parts: Parts
    reverted: list[dict[str, str]] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)


def _decode(parts: Parts, name: str) -> str:
    try:
        return parts[name].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OoxmlError(f"{name}을(를) UTF-8로 읽지 못했다") from exc


def take_snapshot(parts: Parts, guarded: list[GuardedPart]) -> Snapshot:
    """편집 전에 모든 파트와 단위의 지문을 남긴다."""
    prints = {name: fingerprint_part(name, data) for name, data in parts.items()}
    views = {g.name: PartView.of(g, _decode(parts, g.name)) for g in guarded}
    return Snapshot(prints, views)


def _revert(location: str, change: str) -> dict[str, str]:
    return {"location": location, "change": change}


def _well_formed(data: bytes) -> bool:
    """편집된 파트가 올바른 XML인지 확인한다 (제어 문자 등으로 깨진 결과를 걸러낸다)."""
    try:
        ET.fromstring(data)
    except (ET.ParseError, ValueError):
        return False
    return True


def _guard_whole(g: GuardedPart, after: Parts, snap: Snapshot, out: Outcome) -> None:
    if fingerprint_part(g.name, after[g.name]) == snap.part_prints[g.name]:
        return
    if not _well_formed(after[g.name]):
        out.reverted.append(_revert(g.label, "XML이 깨짐"))
        return
    out.parts[g.name] = after[g.name]
    out.changed.append(g.label)


def _guard_units(g: GuardedPart, view: PartView, edited: PartView, out: Outcome) -> str:
    pieces: list[str] = []
    cursor = 0
    for key in view.keys:
        start, end = view.fragments[key]
        pieces.append(view.text[cursor:start])
        cursor = end
        if key in edited.unit_prints and edited.unit_prints[key] == view.unit_prints[key]:
            pieces.append(view.fragment(key))
        elif g.in_scope(key):
            out.changed.append(g.unit_label(key))
            pieces.append(edited.fragment(key) if key in edited.fragments else "")
        else:
            change = "바뀜" if key in edited.fragments else "삭제됨"
            out.reverted.append(_revert(g.unit_label(key), change))
            pieces.append(view.fragment(key))
    pieces.append(view.text[cursor:])
    for key in edited.keys:
        if key not in view.fragments:
            out.reverted.append(_revert(g.unit_label(key), "추가됨"))
    if edited.skeleton_print != view.skeleton_print:
        out.reverted.append(_revert(f"{g.label}의 {g.unit_kind} 밖 구조", "바뀜"))
    return "".join(pieces)


def _guard_part(g: GuardedPart, after: Parts, snap: Snapshot, out: Outcome) -> None:
    if g.name not in after:
        out.reverted.append(_revert(g.label, "삭제됨"))
        return
    if g.whole:
        _guard_whole(g, after, snap, out)
        return
    if not _well_formed(after[g.name]):
        out.reverted.append(_revert(g.label, "XML이 깨짐"))
        return
    try:
        edited = PartView.of(g, _decode(after, g.name))
    except (OoxmlError, ValueError):
        out.reverted.append(_revert(g.label, "XML이 깨짐"))
        return
    text = _guard_units(g, snap.views[g.name], edited, out)
    if text != snap.views[g.name].text:
        out.parts[g.name] = text.encode("utf-8")


def restore(before: Parts, after: Parts, guarded: list[GuardedPart], snap: Snapshot) -> Outcome:
    """편집 결과에서 범위 밖 변경을 원본으로 되돌린다. 원본을 뼈대로 범위 안 변경만 옮긴다."""
    out = Outcome(parts=dict(before))
    names = {g.name for g in guarded}
    for g in guarded:
        _guard_part(g, after, snap, out)
    for name in before:
        if name in names:
            continue
        if name not in after:
            out.reverted.append(_revert(f"파일 {name}", "삭제됨"))
        elif fingerprint_part(name, after[name]) != snap.part_prints[name]:
            out.reverted.append(_revert(f"파일 {name}", "바뀜"))
    for name in after:
        if name not in before:
            out.reverted.append(_revert(f"파일 {name}", "추가됨"))
    return out


def guard_edit(
    kind: str, data: bytes, scope: Any, edit: Any, editor: Editor = apply_edit
) -> tuple[bytes, Outcome]:
    """문서 바이트에 편집을 적용하고 범위 밖 변경을 되돌린 결과 바이트와 내역을 돌려준다."""
    try:
        package = Package.read(data)
    except zipfile.BadZipFile as exc:
        raise GuardError("input", f"문서 파일(zip)을 열지 못했다: {exc}") from exc
    try:
        guarded = layout(kind, package.parts, scope)
        snap = take_snapshot(package.parts, guarded)
    except (ScopeError, OoxmlError) as exc:
        raise GuardError("scope", str(exc)) from exc
    try:
        edited = editor(kind, dict(package.parts), scope, edit)
    except (ScopeError, OoxmlError) as exc:
        raise GuardError("edit", str(exc)) from exc
    outcome = restore(package.parts, edited, guarded, snap)
    return package.write(outcome.parts), outcome


def summary(outcome: Outcome) -> str:
    """결과 문구. 범위 밖 변경이 없으면 '범위 밖 변경 0곳'이 들어간다."""
    text = f"범위 안 변경 {len(outcome.changed)}곳 적용, 범위 밖 변경 {len(outcome.reverted)}곳"
    return text + (" 되돌림" if outcome.reverted else "")


def _publish(data: bytes, final: Path) -> None:
    """임시 파일에 쓴 뒤 원자적으로 교체한다. 실패하면 임시 파일을 지운다."""
    partial = final.with_name(f".{final.name}.part")
    try:
        partial.write_bytes(data)
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _generate(
    input: str, scope: Any, edit: Any, out_dir: str | None, run_id: str
) -> dict[str, Any]:
    source = Path(input)
    kind = source.suffix.lower()
    if kind not in KINDS or not source.is_file():
        raise GuardError("input", f"읽을 수 있는 pptx·xlsx·docx 파일이 아니다: {input}")
    data, outcome = guard_edit(kind, source.read_bytes(), scope, edit)
    target_dir = Path(out_dir) if out_dir else get_home() / "outputs"
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / f"{output_stem(run_id)}{kind}"
    _publish(data, final)
    return {
        "path": str(final),
        "summary": summary(outcome),
        "run_id": run_id,
        "changed": outcome.changed,
        "out_of_scope_changes": len(outcome.reverted),
        "reverted": outcome.reverted,
    }


def guarded_edit(
    input: str, scope: dict[str, Any], edit: dict[str, Any], out_dir: str | None = None
) -> str:
    """편집 범위를 지키며 pptx·xlsx·docx를 고친다. 범위 밖이 바뀌면 원래대로 되돌린다.

    scope(편집 범위):
    - pptx: {"slides": [2], "shapes": [2]} — 슬라이드 번호(1부터)와 도형 id.
      shapes를 빼면 슬라이드 전체.
    - xlsx: {"sheet": 1, "range": "C2:C4"} — 시트 번호(1부터)와 셀 범위.
    - docx: {"paragraphs": [3]} — 본문 문단 번호(0부터).
    edit(편집 내용):
    - pptx: {"set_text": [{"slide": 2, "shape": 2, "text": "새 제목"}]}
    - xlsx: {"set_cells": {"C2": 999}}
    - docx: {"set_paragraphs": {"3": "새 문장"}}
    결과는 JSON 문자열이다: path, summary, changed(범위 안에서 바뀐 위치),
    out_of_scope_changes(되돌린 곳 수), reverted(되돌린 위치 location·변경 change 목록).
    out_dir을 주지 않으면 DOCMILL_HOME/outputs에 저장한다. 실패하면 error와 step을 담는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        result = _generate(input, scope, edit, out_dir, run_id)
    except GuardError as exc:
        result = {"error": exc.message, "step": exc.step, "run_id": run_id}
    except Exception as exc:  # file failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected", "run_id": run_id}
    return json.dumps(result, ensure_ascii=False)
