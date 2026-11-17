"""OOXML 패키지를 바이트 그대로 다루는 도우미 (FR-31).

XML을 파싱해 다시 쓰면 접두사·따옴표·속성 순서가 바뀌어 원본과 바이트가 달라진다.
그래서 이 모듈은 태그를 훑어 요소의 바이트 위치(span)만 찾고, 원본 문자열을 잘라 붙인다.
지문은 요소를 C14N으로 정규화한 뒤 SHA-256으로 만든다.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from xml.etree import ElementTree as ET

_TOKEN = re.compile(
    r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<!(?:\"[^\"]*\"|'[^']*'|[^'\">])*>"
    r"|<(?:\"[^\"]*\"|'[^']*'|[^'\">])*>",
    re.S,
)
_NAME = re.compile(r"</?\s*([^\s/>]+)")
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class OoxmlError(Exception):
    """문서 구조를 해석하지 못했을 때 발생한다."""


@dataclass(frozen=True)
class Span:
    """XML 문자열 안 요소 하나의 위치. `path`는 부모 요소들의 로컬 이름이다."""

    start: int
    end: int
    name: str
    path: tuple[str, ...]


def local(name: str) -> str:
    """`p:sp` → `sp`처럼 접두사를 뗀 이름."""
    return name.rsplit(":", 1)[-1]


def element_spans(text: str, want: Callable[[str, tuple[str, ...]], bool]) -> list[Span]:
    """`want(로컬 이름, 부모 경로)`가 참인 요소의 위치를 문서 순서로 돌려준다 (중첩되면 바깥만)."""
    stack: list[tuple[str, int]] = []
    found: list[Span] = []
    open_at: int | None = None
    open_depth = -1
    path: tuple[str, ...] = ()
    for m in _TOKEN.finditer(text):
        token = m.group(0)
        if token.startswith(("<!", "<?")):
            continue
        name_match = _NAME.match(token)
        if name_match is None:
            raise OoxmlError(f"태그를 해석하지 못했다: {token[:40]}")
        name = local(name_match.group(1))
        if token.startswith("</"):
            if not stack or stack[-1][0] != name:
                raise OoxmlError(f"닫는 태그가 맞지 않는다: {token[:40]}")
            _, begin = stack.pop()
            if open_at is not None and len(stack) == open_depth:
                found.append(Span(begin, m.end(), name, path))
                open_at = None
            continue
        parents = tuple(n for n, _ in stack)
        if open_at is None and want(name, parents):
            if token.endswith("/>"):
                found.append(Span(m.start(), m.end(), name, parents))
                continue
            open_at, open_depth, path = m.start(), len(stack), parents
        if not token.endswith("/>"):
            stack.append((name, m.start()))
    if stack:
        raise OoxmlError("닫히지 않은 태그가 있다")
    return found


def root_tags(text: str) -> tuple[str, str]:
    """루트 요소의 여는 태그 문자열과 닫는 태그 문자열."""
    for m in _TOKEN.finditer(text):
        token = m.group(0)
        if token.startswith(("<!", "<?")):
            continue
        name_match = _NAME.match(token)
        if name_match is None or token.endswith("/>"):
            break
        return token, f"</{name_match.group(1)}>"
    raise OoxmlError("루트 요소를 찾지 못했다")


def fingerprint_xml(fragment: str) -> str:
    """XML 조각을 C14N으로 정규화한 SHA-256 지문. 해석할 수 없으면 원문 바이트로 만든다."""
    try:
        canonical = ET.canonicalize(xml_data=fragment)
    except ET.ParseError:
        canonical = fragment
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fingerprint_part(name: str, data: bytes) -> str:
    """파트 하나의 지문. XML 파트는 정규화하고, 나머지는 바이트 그대로 해시한다."""
    if name.endswith((".xml", ".rels")):
        try:
            return fingerprint_xml(data.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Package:
    """zip 패키지의 파트(이름 순서 보존)와 각 항목 정보."""

    infos: tuple[zipfile.ZipInfo, ...]
    parts: dict[str, bytes]

    @classmethod
    def read(cls, data: bytes) -> Package:
        """zip 바이트를 읽는다."""
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = tuple(z.infolist())
            parts = {i.filename: z.read(i.filename) for i in infos}
        return cls(infos, parts)

    def write(self, parts: dict[str, bytes]) -> bytes:
        """원본 항목 순서·날짜·압축 방식대로 zip 바이트를 만든다 (같은 입력 → 같은 바이트)."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            for info in self.infos:
                if info.filename not in parts:
                    continue
                entry = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                entry.compress_type = info.compress_type
                entry.external_attr = info.external_attr
                entry.create_system = info.create_system
                z.writestr(entry, parts[info.filename])
        return buffer.getvalue()


def rels(parts: dict[str, bytes], part: str) -> dict[str, str]:
    """파트의 관계 `Id → 대상 파트 경로`(패키지 기준)."""
    folder, name = posixpath.split(part)
    rels_name = posixpath.join(folder, "_rels", name + ".rels")
    if rels_name not in parts:
        return {}
    root = ET.fromstring(parts[rels_name])
    return {
        rel.get("Id", ""): _resolve(folder, rel.get("Target", ""))
        for rel in root.iter(f"{_REL}Relationship")
    }


def _resolve(folder: str, target: str) -> str:
    """관계 대상을 패키지 기준 경로로 바꾼다 (`/xl/...`처럼 절대 경로도 받는다)."""
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(folder, target))


def ordered_targets(parts: dict[str, bytes], part: str, tag: str) -> list[str]:
    """`part`에서 `tag` 요소의 `r:id`를 문서 순서대로 따라가 대상 파트 경로를 돌려준다."""
    if part not in parts:
        raise OoxmlError(f"{part}가 없다")
    links = rels(parts, part)
    root = ET.fromstring(parts[part])
    return [links[el.get(f"{_R}id", "")] for el in root.iter(tag) if el.get(f"{_R}id") in links]
