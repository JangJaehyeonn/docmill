"""양식 규칙 등록·조회 (FR-18).

양식 PPT의 첫 제목 도형에서 제목 규칙(크기·색·정렬·글꼴)을 읽어 이름 붙여
`DOCMILL_HOME/templates/<이름>.json`에 저장한다. 읽기는 표준 라이브러리로 pptx(zip) XML만
들여다보며, 문서를 고치는 일은 하지 않는다. 같은 양식이면 같은 JSON이 나온다.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from docmill.runlog import get_home

_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}
TITLE_TYPES = ("title", "ctrTitle")
_ALIGN = {"l": "left", "ctr": "center", "r": "right", "just": "justify"}
_SLIDE = re.compile(r"ppt/slides/slide(\d+)\.xml")
_BAD_NAME = re.compile(r"[\x00-\x1f/\\]|^\.+$")


class TemplateError(Exception):
    """양식 등록·조회가 실패했을 때 발생한다. 메시지는 사용자에게 보여 줄 한국어다."""


def _dir(home: Path | None) -> Path:
    return get_home(home) / "templates"


def _path(name: str, home: Path | None) -> Path:
    if not name.strip() or _BAD_NAME.search(name):
        raise TemplateError(f"양식 이름이 올바르지 않다: {name!r}")
    return _dir(home) / f"{name}.json"


def _is_title(sp: ElementTree.Element) -> bool:
    ph = sp.find("p:nvSpPr/p:nvPr/p:ph", _NS)
    return ph is not None and ph.get("type") in TITLE_TYPES


def _title_rules(sp: ElementTree.Element) -> dict[str, Any]:
    rules: dict[str, Any] = {}
    ppr = sp.find(".//a:pPr[@algn]", _NS)
    if ppr is not None:
        algn = ppr.get("algn", "")
        rules["title_align"] = _ALIGN.get(algn, algn)
    rpr = sp.find(".//a:rPr", _NS)
    if rpr is not None:
        if rpr.get("sz"):
            rules["title_size"] = int(rpr.get("sz", "0")) / 100
        color = rpr.find("a:solidFill/a:srgbClr", _NS)
        if color is not None:
            rules["title_color"] = str(color.get("val", "")).upper()
        latin = rpr.find("a:latin", _NS)
        if latin is not None:
            rules["font"] = latin.get("typeface")
    return rules


def extract_rules(source: Path) -> dict[str, Any]:
    """양식 pptx의 첫 제목 도형에서 규칙을 뽑는다. 못 찾으면 `TemplateError`."""
    if source.suffix.lower() != ".pptx" or not source.is_file():
        raise TemplateError(f"읽을 수 있는 .pptx 양식 파일이 아니다: {source}")
    try:
        with zipfile.ZipFile(source) as z:
            slides = sorted(
                (int(m.group(1)), n) for n in z.namelist() if (m := _SLIDE.fullmatch(n))
            )
            for _, member in slides:
                root = ElementTree.fromstring(z.read(member))
                for sp in root.iterfind(".//p:sp", _NS):
                    if _is_title(sp) and (rules := _title_rules(sp)):
                        return rules
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise TemplateError(f"양식 파일을 읽지 못했다: {exc}") from exc
    raise TemplateError("양식에서 제목 서식을 찾지 못했다")


def register_template(name: str, source: Path | str, home: Path | None = None) -> dict[str, Any]:
    """양식 PPT에서 규칙을 뽑아 `name`으로 저장하고, 저장한 규칙을 돌려준다."""
    target = _path(name, home)
    rules = extract_rules(Path(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rules, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return rules


def load_template(name: str, home: Path | None = None) -> dict[str, Any]:
    """저장된 양식 규칙을 읽는다. 없으면 `TemplateError`."""
    target = _path(name, home)
    if not target.is_file():
        raise TemplateError(f"등록된 양식이 없다: {name}")
    return dict(json.loads(target.read_text(encoding="utf-8")))


def list_templates(home: Path | None = None) -> list[str]:
    """등록된 양식 이름을 정렬해 돌려준다."""
    folder = _dir(home)
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []
