"""`mask_text` / `unmask_text` 업무 도구 (FR-39).

개인정보(주민등록번호·전화번호·이메일·계좌번호·사람이 등록한 이름)를 `[주민번호1]` 같은 자리표로
바꾸고, 나중에 원래 값으로 되돌린다. 대응표는 이 프로세스의 메모리에만 두며 파일·로그·도구
결과에 쓰지 않는다. 자리표 번호는 종류별로 처음 나온 순서대로 1부터 붙으므로 같은 입력이면
같은 결과가 나온다 (규칙 기반, AI·시각·난수 없음).
"""

from __future__ import annotations

import bisect
import html
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from docmill.tools.hwp import HwpError, is_hwp, read_hwp_lines
from docmill.tools.preflight import _privacy_matches

NAME_KIND = "이름"
_LABELS = {
    "주민등록번호": "주민번호",
    "전화번호": "전화번호",
    "이메일": "이메일",
    "계좌번호": "계좌번호",
}
_PLACEHOLDER = re.compile(r"\[(?:주민번호|전화번호|이메일|계좌번호|이름)\d+\]")
# 이름 앞에 흔히 붙는 라벨 (사람이 이름을 등록하지 않았을 때 문서에서 이름을 찾는 단서).
_NAME_AFTER_LABEL = re.compile(
    r"(?:담당자|담당|성명|이름|작성자|대표자|대표|책임자|신청인|신청자|제출자|문의)\s*[:：]?\s*([가-힣]{2,4})"
)
_PARTICLES = "의은는이가을를도와과"
MAX_DOC_CHARS = 8000
MAX_XML_BYTES = 20 * 1024 * 1024

Found = tuple[int, int, str, str]


class MaskTable:
    """원래 값 ↔ 자리표 대응표. 메모리에만 둔다."""

    def __init__(self, reverse: dict[str, str] | None = None) -> None:
        self.reverse: dict[str, str] = dict(reverse or {})
        self.reserved: set[str] = set()  # 원문에 이미 있는 자리표 모양 (새로 발급하지 않는다)
        self.forward: dict[str, str] = {v: k for k, v in self.reverse.items()}

    def _placeholder(self, kind: str, raw: str) -> str:
        known = self.forward.get(raw)
        if known:
            return known
        number = sum(1 for p in self.reverse if p.startswith(f"[{kind}")) + 1
        while f"[{kind}{number}]" in self.reverse or f"[{kind}{number}]" in self.reserved:
            number += 1
        placeholder = f"[{kind}{number}]"
        self.reverse[placeholder] = raw
        self.forward[raw] = placeholder
        return placeholder

    def reserve(self, text: str) -> None:
        """원문에 이미 있는 자리표 모양을 예약한다. 같은 번호를 새로 발급하지 않는다."""
        self.reserved.update(
            m.group(0) for m in _PLACEHOLDER.finditer(text) if m.group(0) not in self.reverse
        )

    def mask(self, text: str, names: list[str] | None = None) -> tuple[str, list[dict[str, str]]]:
        """가린 문장과 쓰인 자리표 목록(`placeholder`·`kind`, 원래 값 없음)을 돌려준다."""
        self.reserve(text)
        found: list[Found] = []
        for kind, raw in dict.fromkeys(_privacy_matches(text)):
            found += [
                (m.start(), m.end(), _LABELS[kind], raw) for m in re.finditer(re.escape(raw), text)
            ]
        for name in sorted({n.strip() for n in names or [] if n.strip()}, key=lambda n: -len(n)):
            found += [
                (m.start(), m.end(), NAME_KIND, name) for m in re.finditer(re.escape(name), text)
            ]
        out: list[str] = []
        used: list[dict[str, str]] = []
        cursor = 0
        for start, end, kind, raw in _non_overlapping(found):
            ph = self._placeholder(kind, raw)
            out.append(text[cursor:start] + ph)
            cursor = end
            if all(u["placeholder"] != ph for u in used):
                used.append({"placeholder": ph, "kind": kind})
        out.append(text[cursor:])
        return "".join(out), used

    def unmask(self, text: str) -> str:
        """자리표를 원래 값으로 되돌린다. 대응표에 없는 자리표는 그대로 둔다."""
        return _PLACEHOLDER.sub(lambda m: self.reverse.get(m.group(0), m.group(0)), text)


def _non_overlapping(found: list[Found]) -> list[Found]:
    """겹치는 구간은 먼저 잡힌 것(개인정보 형식 우선)만 남기고 위치 순으로 정렬한다."""
    kept: list[Found] = []  # 시작 위치 순으로 유지해 이분 탐색으로 이웃만 비교한다
    for item in found:
        i = bisect.bisect_left(kept, (item[0],))
        before_ok = i == 0 or kept[i - 1][1] <= item[0]
        after_ok = i == len(kept) or kept[i][0] >= item[1]
        if before_ok and after_ok:
            kept.insert(i, item)
    return kept


def detect_names(text: str) -> list[str]:
    """'담당자 홍길동'처럼 라벨 뒤에 나온 이름을 찾는다 (등록하지 않은 이름용 단서)."""
    names: list[str] = []
    for m in _NAME_AFTER_LABEL.finditer(text):
        name = m.group(1)
        if len(name) == 4 and name[-1] in _PARTICLES:
            name = name[:-1]
        if name not in names:
            names.append(name)
    return names


_SESSION = MaskTable()


def mask_text(text: str, names: list[str] | None = None) -> str:
    """문장 속 개인정보를 `[주민번호1]` 같은 자리표로 바꾼다. 원래 값은 결과에 남기지 않는다.

    `names`는 사람이 등록한 이름 목록이다. 대응표는 서버 메모리에만 있고,
    `unmask_text`가 같은 세션에서 되돌린다.
    """
    masked, used = _SESSION.mask(text, names)
    return json.dumps({"masked": masked, "placeholders": used}, ensure_ascii=False)


def unmask_text(text: str) -> str:
    """자리표를 원래 값으로 되돌린다 (같은 서버 세션에서 `mask_text`로 가린 것만)."""
    return json.dumps({"text": _SESSION.unmask(text)}, ensure_ascii=False)


# --- studio 연결용 (CLI) -------------------------------------------------------------


def _xml_lines(data: bytes) -> list[str]:
    xml = re.sub(r"</(?:w:p|a:p|si)>", "\n", data.decode("utf-8", "replace"))
    return html.unescape(re.sub(r"<[^>]+>", "", xml)).splitlines()


def read_document_text(path: Path, limit: int | None = MAX_DOC_CHARS) -> str:
    """문서 본문 글자를 줄 단위로 읽는다. 읽지 못하면 빈 문자열. `limit`이 None이면 안 자른다."""
    if is_hwp(path):
        try:
            return "\n".join(read_hwp_lines(path))[:limit]
        except HwpError:
            return ""
    lines: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            slides = sorted(
                (n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                key=lambda n: int(re.findall(r"\d+", n)[0]),
            )
            for part in ["word/document.xml", *slides, "xl/sharedStrings.xml"]:
                if part in names and z.getinfo(part).file_size <= MAX_XML_BYTES:
                    lines += _xml_lines(z.read(part))
    except (OSError, zipfile.BadZipFile):
        return ""
    return "\n".join(line for line in lines if line.strip())[:limit]


def _cut(text: str, limit: int) -> str:
    """자리표 중간에서 끊기지 않도록 `limit`자 이내로 자른다."""
    if len(text) <= limit:
        return text
    for m in _PLACEHOLDER.finditer(text):
        if m.start() < limit < m.end():
            return text[: m.start()]
    return text[:limit]


def mask_documents(request: dict[str, Any]) -> dict[str, Any]:
    """studio가 에이전트에 넘길 내용을 가린다.

    요청: `paths`(문서 경로), `message`(사용자 메시지), `names`(등록한 이름), `table`(이전 대응표).
    결과: `documents`(경로·가린 본문), `message`(가린 메시지), `table`(갱신된 대응표).
    """
    table = MaskTable({str(k): str(v) for k, v in dict(request.get("table") or {}).items()})
    found = [str(n) for n in request.get("names") or []]
    # 길이를 자르기 전에 전체 본문을 가려야 경계에 걸친 이름이 새지 않는다.
    raw = [(str(p), read_document_text(Path(str(p)), None)) for p in request.get("paths") or []]
    message_raw = str(request.get("message") or "")
    for _, text in [*raw, ("", message_raw)]:
        table.reserve(text)
        found += [n for n in detect_names(text) if n not in found]
    docs = [{"path": p, "text": _cut(table.mask(t, found)[0], MAX_DOC_CHARS)} for p, t in raw]
    message = table.mask(message_raw, found)[0]
    return {"documents": docs, "message": message, "table": table.reverse}


def unmask_document(path: Path, table: dict[str, str]) -> int:
    """문서 파일 안의 자리표를 원래 값으로 되돌린다. 되돌린 자리표 개수를 돌려준다."""
    mapping = MaskTable({str(k): str(v) for k, v in table.items()})
    count = 0
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        blobs = {i.filename: z.read(i.filename) for i in infos}
    for info in infos:
        if not info.filename.endswith(".xml"):
            continue
        text = blobs[info.filename].decode("utf-8", "replace")
        hits = [m for m in _PLACEHOLDER.finditer(text) if m.group(0) in mapping.reverse]
        if hits:
            count += len(hits)
            new = _PLACEHOLDER.sub(
                lambda m: html.escape(mapping.reverse.get(m.group(0), m.group(0)), quote=True),
                text,
            )
            blobs[info.filename] = new.encode("utf-8")
    if count:
        tmp = path.with_name(path.name + ".unmask.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for info in infos:
                out.writestr(info, blobs[info.filename])
        tmp.replace(path)
    return count
