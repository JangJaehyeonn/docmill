"""`insert_image` 업무 도구 (FR-42).

설명 문장으로 이미지(PNG)를 만들어 지정한 슬라이드·문단에 넣는다. 이미지 제공자는 바꿀 수 있고
(`openai`·`gemini`·테스트용 `fake`) 키는 환경 변수로만 받는다. 키가 없으면 실패하지 않고
기능이 꺼졌다는 이유와 필요한 환경 변수 이름을 돌려준다.

- pptx·docx: `guarded_edit`과 같은 지키기(`guard_edit`)로 범위(슬라이드 한 장·문단 하나)만 바꾼다.
  그림 파일·관계·콘텐츠 형식 항목은 범위 밖 파트라 지키기를 거친 뒤에 덧붙인다.
- hwp·hwpx: rhwp 어댑터가 문단 뒤에 새 문단을 만들어 그림을 넣고 hwp로 저장한다.
"""

from __future__ import annotations

import base64
import json
import os
import posixpath
import re
import struct
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

from docmill.engines.rhwp import insert_picture
from docmill.runlog import current_run_id, get_home, new_run_id, output_stem
from docmill.tools.edits import Parts, _start_tag
from docmill.tools.guard import GuardError, Outcome, guard_edit, summary
from docmill.tools.hwp import HWP_SUFFIXES
from docmill.tools.ooxml import Package, element_spans
from docmill.tools.scope import DOCUMENT_PART, ScopeError, numbered, slide_parts

PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "fake": "DOCMILL_FAKE_IMAGE_PNG",
}
DEFAULT_PROVIDER_ENV = "DOCMILL_IMAGE_PROVIDER"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_REQUEST_TIMEOUT = 60
_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)
_EMU_PER_PX = 9525
_HWP_UNIT_PER_PX = 75
_HWP_MAX_WIDTH = 30000


class ImageDisabled(Exception):
    """이미지 제공자 키가 없어 기능이 꺼져 있을 때 발생한다. `env`는 필요한 환경 변수 이름이다."""

    def __init__(self, provider: str, env: str) -> None:
        super().__init__(provider)
        self.provider = provider
        self.env = env


def png_size(data: bytes) -> tuple[int, int]:
    """PNG 바이트의 (너비, 높이) px. PNG가 아니면 ValueError."""
    if data[:8] != _PNG_MAGIC or data[12:16] != b"IHDR" or len(data) < 24:
        raise ValueError("제공자가 PNG 이미지를 돌려주지 않았다")
    width, height = struct.unpack(">II", data[16:24])
    if width < 1 or height < 1:
        raise ValueError("PNG 크기가 올바르지 않다")
    return int(width), int(height)


def _post_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as response:
            return dict(json.loads(response.read()))
    except urllib.error.HTTPError as exc:
        raise ValueError(f"이미지 제공자가 오류를 돌려줬다 (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError(f"이미지 제공자에 연결하지 못했다: {type(exc).__name__}") from exc


def _openai(prompt: str, key: str) -> bytes:
    body = {"model": "gpt-image-1", "prompt": prompt, "size": "1024x1024", "n": 1}
    reply = _post_json(
        "https://api.openai.com/v1/images/generations", {"Authorization": f"Bearer {key}"}, body
    )
    try:
        return base64.b64decode(reply["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("OpenAI 응답에서 이미지를 찾지 못했다") from exc


def _gemini(prompt: str, key: str) -> bytes:
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    reply = _post_json(url, {"x-goog-api-key": key}, body)
    try:
        for part in reply["candidates"][0]["content"]["parts"]:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline:
                return base64.b64decode(inline["data"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("Gemini 응답에서 이미지를 찾지 못했다") from exc
    raise ValueError("Gemini 응답에서 이미지를 찾지 못했다")


def _fake(_prompt: str, path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise ValueError(f"가짜 제공자의 PNG를 읽지 못했다: {exc}") from exc


def generate_image(provider: str, prompt: str) -> bytes:
    """설명 문장으로 PNG 바이트를 만든다. 키가 없으면 ImageDisabled, 그 밖의 실패는 ValueError."""
    if provider not in PROVIDER_KEYS:
        raise ValueError(f"알 수 없는 제공자: {provider} ({', '.join(sorted(PROVIDER_KEYS))})")
    env = PROVIDER_KEYS[provider]
    secret = os.environ.get(env, "").strip()
    if not secret:
        raise ImageDisabled(provider, env)
    makers = {"openai": _openai, "gemini": _gemini, "fake": _fake}
    data = makers[provider](prompt, secret)
    png_size(data)
    return data


def _slide_picture(
    shape_id: int, rid: str, size: tuple[int, int], slide_box: tuple[int, int]
) -> str:
    """슬라이드 가운데에 놓을 `p:pic` (너비는 슬라이드의 절반 이하, 비율 유지)."""
    width_px, height_px = size
    scale = min(slide_box[0] / 2 / width_px, slide_box[1] / 2 / height_px)
    cx, cy = max(1, int(width_px * scale)), max(1, int(height_px * scale))
    x, y = (slide_box[0] - cx) // 2, (slide_box[1] - cy) // 2
    return (
        f'<p:pic xmlns:r="{_REL_NS}"><p:nvPicPr><p:cNvPr id="{shape_id}" name="Image {shape_id}"/>'
        '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
        f'<p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
    )


def _slide_box(parts: Parts) -> tuple[int, int]:
    m = re.search(
        rb'<p:sldSz[^>]*\bcx="(\d+)"[^>]*\bcy="(\d+)"', parts.get("ppt/presentation.xml", b"")
    )
    return (int(m.group(1)), int(m.group(2))) if m else (9144000, 6858000)


def _add_to_slide(parts: Parts, spec: dict[str, Any]) -> None:
    name = numbered(slide_parts(parts), spec["slide"], "슬라이드")
    text = parts[name].decode("utf-8")
    ids = [int(i) for i in re.findall(r'<(?:\w+:)?cNvPr\b[^>]*?\sid="(\d+)"', text)]
    pic = _slide_picture(max(ids, default=1) + 1, spec["rid"], spec["size"], _slide_box(parts))
    end = re.search(r"</(?:\w+:)?spTree>", text)
    if end is None:
        raise ScopeError("슬라이드에 도형 영역(spTree)이 없다")
    parts[name] = (text[: end.start()] + pic + text[end.start() :]).encode("utf-8")


def _inline_drawing(doc_id: int, rid: str, size: tuple[int, int], description: str) -> str:
    cx, cy = size[0] * _EMU_PER_PX, size[1] * _EMU_PER_PX
    limit = 5486400  # 15 cm: keeps the picture inside the page width
    if cx > limit:
        cx, cy = limit, max(1, cy * limit // cx)
    alt = quoteattr(description)
    return (
        "<w:r><w:drawing>"
        f'<wp:inline xmlns:wp="{_WP_NS}" distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:docPr id="{doc_id}" name="Image {doc_id}" descr={alt}/>'
        f'<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="{_A_NS}" noChangeAspect="1"/>'
        "</wp:cNvGraphicFramePr>"
        f'<a:graphic xmlns:a="{_A_NS}">'
        f'<a:graphicData uri="{_PIC_NS}">'
        f'<pic:pic xmlns:pic="{_PIC_NS}">'
        '<pic:nvPicPr><pic:cNvPr id="0" name="image.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}" xmlns:r="{_REL_NS}"/>'
        "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )


def _add_to_paragraph(parts: Parts, spec: dict[str, Any]) -> None:
    text = parts[DOCUMENT_PART].decode("utf-8")
    spans = element_spans(text, lambda n, p: n == "p" and p[-1:] == ("body",))
    index = spec["paragraph"]
    if not 0 <= index < len(spans):
        raise ScopeError(f"문단 번호가 올바르지 않다: {index + 1} (1~{len(spans)})")
    span = spans[index]
    fragment = text[span.start : span.end]
    start, prefix = _start_tag(fragment)
    ids = [int(i) for i in re.findall(r'<wp:docPr\b[^>]*?\sid="(\d+)"', text)]
    drawing = _inline_drawing(
        max(ids, default=0) + 1, spec["rid"], spec["size"], spec["description"]
    )
    closing = f"</{prefix}p>"
    # A self-closing paragraph (`<w:p/>`) has no body; otherwise keep everything before the end tag.
    body = fragment[len(start) : -len(closing)] if fragment.endswith(closing) else ""
    parts[DOCUMENT_PART] = (
        text[: span.start] + start + body + drawing + closing + text[span.end :]
    ).encode("utf-8")


def _editor(kind: str, parts: Parts, _scope: dict[str, Any], edit: dict[str, Any]) -> Parts:
    edited = dict(parts)
    if kind == ".pptx":
        _add_to_slide(edited, edit["add_image"])
    else:
        _add_to_paragraph(edited, edit["add_image"])
    return edited


def _next_rid(rels_text: str) -> str:
    numbers = [int(n) for n in re.findall(r'Id="rId(\d+)"', rels_text)]
    return f"rId{max(numbers, default=0) + 1}"


def _rels_name(part: str) -> str:
    folder, _, name = part.rpartition("/")
    return f"{folder}/_rels/{name}.rels"


def _image_name(parts: Parts, folder: str) -> str:
    taken = {n for n in parts if n.startswith(f"{folder}/")}
    number = 1
    while f"{folder}/image{number}.png" in taken:
        number += 1
    return f"{folder}/image{number}.png"


def _attach_image(kind: str, data: bytes, part: str, rid: str, png: bytes) -> bytes:
    """범위 밖 파트(그림 파일·관계·콘텐츠 형식)를 지키기 뒤에 덧붙인다."""
    package = Package.read(data)
    parts = dict(package.parts)
    media = _image_name(parts, "ppt/media" if kind == ".pptx" else "word/media")
    rels_name = _rels_name(part)
    target = posixpath.relpath(media, part.rpartition("/")[0])
    relation = f'<Relationship Id="{rid}" Type="{_REL_TYPE}" Target="{target}"/>'
    rels = parts.get(rels_name, b"").decode("utf-8")
    if not rels:
        rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>'
    parts[rels_name] = rels.replace("</Relationships>", relation + "</Relationships>").encode(
        "utf-8"
    )
    types = parts["[Content_Types].xml"].decode("utf-8")
    if not re.search(r'<Default\b[^>]*\bExtension="png"', types, re.IGNORECASE):
        default = '<Default Extension="png" ContentType="image/png"/>'
        types = re.sub(r"(<Types\b[^>]*>)", lambda m: m.group(1) + default, types, count=1)
        parts["[Content_Types].xml"] = types.encode("utf-8")
    parts[media] = png
    infos = list(package.infos)
    for added in (media, rels_name):
        if added not in {i.filename for i in infos}:
            info = zipfile.ZipInfo(added, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            infos.append(info)
    return Package(tuple(infos), package.parts).write(parts)


def _ooxml(source: Path, kind: str, args: dict[str, Any], png: bytes) -> tuple[bytes, Outcome]:
    data = source.read_bytes()
    size = png_size(png)
    try:
        package = Package.read(data)
        if kind == ".pptx":
            part = numbered(slide_parts(package.parts), args["slide"], "슬라이드")
            scope: dict[str, Any] = {"slides": [args["slide"]]}
            spec: dict[str, Any] = {"slide": args["slide"]}
        else:
            part = DOCUMENT_PART
            paragraph = args["after_paragraph"]
            scope = {"paragraphs": [paragraph - 1]}
            spec = {"paragraph": paragraph - 1, "description": args["prompt"]}
    except zipfile.BadZipFile as exc:
        raise GuardError("input", f"문서 파일(zip)을 열지 못했다: {exc}") from exc
    except (ScopeError, KeyError, TypeError) as exc:
        raise GuardError("scope", str(exc) or "위치 인자가 올바르지 않다") from exc
    rid = _next_rid(package.parts.get(_rels_name(part), b"").decode("utf-8"))
    spec.update(rid=rid, size=size)
    edited, outcome = guard_edit(kind, data, scope, {"add_image": spec}, editor=_editor)
    return _attach_image(kind, edited, part, rid, png), outcome


def _hwp(source: Path, target: Path, args: dict[str, Any], png: bytes) -> None:
    width_px, height_px = png_size(png)
    width = min(width_px * _HWP_UNIT_PER_PX, _HWP_MAX_WIDTH)
    height = max(1, height_px * width // width_px)
    paragraph = args.get("after_paragraph")
    if isinstance(paragraph, bool) or not isinstance(paragraph, int):
        raise GuardError("scope", "hwp는 after_paragraph(1부터 문단 번호)가 필요하다")
    with tempfile.TemporaryDirectory(prefix="docmill-image-") as tmp:
        work = Path(tmp).resolve()
        image = work / "image.png"
        image.write_bytes(png)
        staged = work / f"input{source.suffix.lower()}"
        staged.write_bytes(source.read_bytes())
        out = work / "output.hwp"
        result = insert_picture(
            staged, out, image, paragraph, (width, height, width_px, height_px), args["prompt"]
        )
        if not result.ok:
            raise GuardError("edit", result.message)
        target.write_bytes(out.read_bytes())


def _disabled(exc: ImageDisabled, run_id: str) -> dict[str, Any]:
    return {
        "disabled": True,
        "message": f"이미지 생성이 꺼져 있어요. 환경 변수 {exc.env}를 설정하면 켜져요 "
        f"(제공자: {exc.provider}).",
        "env": exc.env,
        "run_id": run_id,
    }


def _insert(
    document: str,
    prompt: str,
    slide: int | None,
    after_paragraph: int | None,
    provider: str | None,
    out_dir: str | None,
    run_id: str,
) -> dict[str, Any]:
    source = Path(document)
    kind = source.suffix.lower()
    if kind not in (".pptx", ".docx", *HWP_SUFFIXES) or not source.is_file():
        raise GuardError("input", f"읽을 수 있는 pptx·docx·hwp 파일이 아니다: {document}")
    if not prompt.strip():
        raise GuardError("input", "이미지 설명(prompt)이 비어 있다")
    name = provider or os.environ.get(DEFAULT_PROVIDER_ENV, "") or "openai"
    try:
        png = generate_image(name, prompt)
    except ValueError as exc:
        raise GuardError("generate", str(exc)) from exc
    args = {"slide": slide, "after_paragraph": after_paragraph, "prompt": prompt}
    target_dir = Path(out_dir) if out_dir else get_home() / "outputs"
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / f"{output_stem(run_id)}{'.hwp' if kind in HWP_SUFFIXES else kind}"
    result: dict[str, Any] = {"path": str(final), "run_id": run_id, "provider": name}
    if kind in HWP_SUFFIXES:
        _hwp(source, final, args, png)
        result["summary"] = f"문단 {after_paragraph} 뒤에 이미지를 넣었어요"
    else:
        data, outcome = _ooxml(source, kind, args, png)
        final.write_bytes(data)
        result.update(summary=summary(outcome), changed=outcome.changed, reverted=outcome.reverted)
        result["out_of_scope_changes"] = len(outcome.reverted)
    return result


def insert_image(
    document: str,
    prompt: str,
    slide: int | None = None,
    after_paragraph: int | None = None,
    provider: str | None = None,
    out_dir: str | None = None,
) -> str:
    """설명 문장으로 이미지를 만들어 슬라이드나 문단 뒤에 넣는다.

    document: pptx·docx·hwp·hwpx 파일 경로. 원본은 그대로 두고 새 파일을 만든다.
    prompt: 만들 이미지를 설명하는 문장.
    slide: pptx에서 이미지를 넣을 슬라이드 번호(1부터). 다른 슬라이드는 바뀌지 않는다.
    after_paragraph: docx·hwp에서 이 문단(1부터) 뒤에 넣는다 (docx는 그 문단 끝에 그림을 붙인다).
    provider: `openai`(OPENAI_API_KEY)·`gemini`(GEMINI_API_KEY).
    생략하면 환경 변수 DOCMILL_IMAGE_PROVIDER, 그것도 없으면 openai.
    키가 없으면 실패하지 않고 {"disabled": true, "message": ..., "env": 환경 변수 이름}을 돌려준다.
    결과는 JSON 문자열이다: path(새 파일), summary, run_id. 실패하면 error와 step을 담는다.
    """
    run_id = current_run_id() or new_run_id()
    try:
        result = _insert(document, prompt, slide, after_paragraph, provider, out_dir, run_id)
    except ImageDisabled as exc:
        result = _disabled(exc, run_id)
    except GuardError as exc:
        result = {"error": exc.message, "step": exc.step, "run_id": run_id}
    except Exception as exc:  # file failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected", "run_id": run_id}
    return json.dumps(result, ensure_ascii=False)
