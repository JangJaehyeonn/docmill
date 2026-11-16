"""양식 규칙 등록·조회 (FR-18).

양식 PPT의 첫 제목 도형에서 제목 규칙(크기·색·정렬·글꼴)을 읽어 이름 붙여
`DOCMILL_HOME/templates/<이름>.json`에 저장한다. 읽기는 표준 라이브러리로 pptx(zip) XML만
들여다보며, 문서를 고치는 일은 하지 않는다. 같은 양식이면 같은 JSON이 나온다.
"""

from __future__ import annotations

import colorsys
import json
import posixpath
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


_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_CLR_MAP = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}


def _read_xml(z: zipfile.ZipFile, name: str) -> ElementTree.Element | None:
    try:
        return ElementTree.fromstring(z.read(name))
    except KeyError:
        return None


def _related(z: zipfile.ZipFile, part: str, kind: str) -> str | None:
    """`part`가 `kind`(관계 유형 끝 이름)로 가리키는 파트 경로를 돌려준다."""
    folder, _, base = part.rpartition("/")
    rels = _read_xml(z, f"{folder}/_rels/{base}.rels")
    if rels is None:
        return None
    for rel in rels.iter(_REL):
        if str(rel.get("Type", "")).endswith(f"/{kind}"):
            joined = posixpath.normpath(posixpath.join(folder, str(rel.get("Target", ""))))
            return joined.lstrip("/")
    return None


def _theme_color(
    theme: ElementTree.Element | None, master: ElementTree.Element | None, name: str
) -> str | None:
    if theme is None:
        return None
    if master is not None:
        cmap = master.find("p:clrMap", _NS)
        if cmap is not None and cmap.get(name):
            name = str(cmap.get(name))
    name = _CLR_MAP.get(name, name)
    slot = theme.find(f"a:themeElements/a:clrScheme/a:{name}", _NS)
    if slot is None:
        return None
    srgb = slot.find("a:srgbClr", _NS)
    if srgb is not None:
        return str(srgb.get("val", "")).upper()
    sysc = slot.find("a:sysClr", _NS)
    return str(sysc.get("lastClr", "")).upper() if sysc is not None else None


def _theme_font(theme: ElementTree.Element | None, face: str, slot: str = "latin") -> str | None:
    kind = "majorFont" if face.startswith("+mj") else "minorFont"
    node = (
        None
        if theme is None
        else theme.find(f"a:themeElements/a:fontScheme/a:{kind}/a:{slot}", _NS)
    )
    return None if node is None else node.get("typeface")


def _ratio(raw: str) -> float:
    """`"75%"`는 100으로, `"75000"`은 100000으로 나눠 0.75로 만든다. 읽을 수 없으면 0."""
    text = raw.strip()
    try:
        if text.endswith("%"):
            return float(text[:-1]) / 100
        return int(text) / 100000
    except ValueError:
        return 0.0


def _apply_transforms(rgb: str, color: ElementTree.Element) -> str:
    """색 요소의 자식 `lumMod`·`lumOff`를 문서 순서대로 HSL 밝기에 적용한다."""
    ops = [c for c in color if c.tag.rsplit("}", 1)[-1] in ("lumMod", "lumOff")]
    if not ops or not re.fullmatch(r"[0-9A-F]{6}", rgb):
        return rgb
    r, g, b = (int(rgb[i : i + 2], 16) / 255 for i in (0, 2, 4))
    h, lum, sat = colorsys.rgb_to_hls(r, g, b)
    for op in ops:
        val = _ratio(op.get("val", "0"))
        lum = lum * val if op.tag.endswith("lumMod") else lum + val
        lum = min(1.0, max(0.0, lum))
    out = colorsys.hls_to_rgb(h, lum, sat)
    return "".join(f"{round(x * 255):02X}" for x in out)


def _read_props(
    algn_src: ElementTree.Element | None,
    run_src: ElementTree.Element | None,
    theme: ElementTree.Element | None,
    master: ElementTree.Element | None,
) -> dict[str, Any]:
    """문단 속성(`algn_src`)과 글자 속성(`run_src`) 한 쌍에서 규칙을 읽는다."""
    rules: dict[str, Any] = {}
    if algn_src is not None and algn_src.get("algn"):
        algn = algn_src.get("algn", "")
        rules["title_align"] = _ALIGN.get(algn, algn)
    if run_src is None:
        return rules
    if run_src.get("sz"):
        rules["title_size"] = int(run_src.get("sz", "0")) / 100
    fill = run_src.find("a:solidFill", _NS)
    if fill is not None:
        srgb = fill.find("a:srgbClr", _NS)
        scheme = fill.find("a:schemeClr", _NS)
        if srgb is not None:
            rules["title_color"] = _apply_transforms(str(srgb.get("val", "")).upper(), srgb)
        elif scheme is not None and (c := _theme_color(theme, master, scheme.get("val", ""))):
            rules["title_color"] = _apply_transforms(c, scheme)
    latin = run_src.find("a:latin", _NS)
    if latin is not None and latin.get("typeface"):
        face = str(latin.get("typeface"))
        font = _theme_font(theme, face) if face.startswith("+") else face
        if font:
            rules["font"] = font
    ea = run_src.find("a:ea", _NS)
    if ea is not None and ea.get("typeface"):
        face = str(ea.get("typeface"))
        east = _theme_font(theme, face, "ea") if face.startswith("+") else face
        if east:
            rules["font_ea"] = east
    return rules


def _direct(sp: ElementTree.Element, theme: Any, master: Any) -> dict[str, Any]:
    ppr = sp.find(".//a:pPr[@algn]", _NS)
    return _read_props(ppr, sp.find(".//a:rPr", _NS), theme, master)


def _lvl1(parent: ElementTree.Element | None, theme: Any, master: Any) -> dict[str, Any]:
    lvl = None if parent is None else parent.find("a:lvl1pPr", _NS)
    run = None if lvl is None else lvl.find("a:defRPr", _NS)
    return _read_props(lvl, run, theme, master)


def _layout_title(layout: ElementTree.Element | None) -> ElementTree.Element | None:
    if layout is None:
        return None
    for sp in layout.iterfind(".//p:sp", _NS):
        if _is_title(sp):
            return sp.find("p:txBody/a:lstStyle", _NS)
    return None


def _title_rules(z: zipfile.ZipFile, slide_part: str, sp: ElementTree.Element) -> dict[str, Any]:
    """제목 규칙을 슬라이드 직접 서식 → 레이아웃 → 마스터 순으로 합친다 (앞이 우선)."""
    layout_part = _related(z, slide_part, "slideLayout")
    master_part = _related(z, layout_part, "slideMaster") if layout_part else None
    theme_part = _related(z, master_part, "theme") if master_part else None
    master = _read_xml(z, master_part) if master_part else None
    theme = _read_xml(z, theme_part) if theme_part else None
    layout = _read_xml(z, layout_part) if layout_part else None
    merged: dict[str, Any] = {}
    layers = (
        _direct(sp, theme, master),
        _lvl1(_layout_title(layout), theme, master),
        _lvl1(
            None if master is None else master.find("p:txStyles/p:titleStyle", _NS), theme, master
        ),
    )
    for layer in layers:
        for key, value in layer.items():
            merged.setdefault(key, value)
    return merged


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
                    if _is_title(sp) and (rules := _title_rules(z, member, sp)):
                        return rules
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise TemplateError(f"양식 파일을 읽지 못했다: {exc}") from exc
    raise TemplateError("양식에서 제목 서식을 찾지 못했다")


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING = re.compile(r"(?:Heading|heading |제목\s?)([123])")
_DOCX_ALIGN = {"both": "justify", "start": "left", "end": "right"}
DOCX_LEVELS = ("1", "2", "3")


def heading_level(style_id: str, name: str = "") -> str | None:
    """스타일 ID나 표시 이름이 제목 1~3이면 수준 문자열을, 아니면 None을 돌려준다."""
    for text in (style_id, name):
        m = _HEADING.fullmatch(text)
        if m:
            return m.group(1)
    return None


def style_names(styles: ElementTree.Element) -> dict[str, str]:
    """`word/styles.xml`의 스타일 ID → 표시 이름 매핑을 만든다."""
    return {s.get(f"{_W}styleId", ""): _w_val(s, "name") or "" for s in styles.iter(f"{_W}style")}


def paragraph_level(p: ElementTree.Element, names: dict[str, str]) -> str | None:
    """문단의 스타일(ID 또는 표시 이름)로 제목 수준을 판정한다."""
    style_id = _w_val(p.find(f"{_W}pPr"), "pStyle") or ""
    return heading_level(style_id, names.get(style_id, ""))


def _w_val(node: ElementTree.Element | None, tag: str, attr: str = "val") -> str | None:
    child = None if node is None else node.find(f"{_W}{tag}")
    return None if child is None else child.get(f"{_W}{attr}")


_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_THEME_COLORS = {
    "text1": "dk1",
    "background1": "lt1",
    "text2": "dk2",
    "background2": "lt2",
    "dark1": "dk1",
    "light1": "lt1",
    "dark2": "dk2",
    "light2": "lt2",
    "hyperlink": "hlink",
    "followedHyperlink": "folHlink",
}
_HEX6 = re.compile(r"[0-9A-Fa-f]{6}")


def _theme_color_hex(theme: ElementTree.Element | None, name: str) -> str | None:
    if theme is None:
        return None
    slot = theme.find(f"{_A}themeElements/{_A}clrScheme/{_A}{_THEME_COLORS.get(name, name)}")
    if slot is None or len(slot) == 0:
        return None
    node = slot[0]
    value = node.get("val") if node.tag == f"{_A}srgbClr" else node.get("lastClr")
    return value.upper() if value and _HEX6.fullmatch(value) else None


def _theme_face(theme: ElementTree.Element | None, ref: str) -> str | None:
    """`majorHAnsi`·`minorEastAsia` 같은 테마 글꼴 참조를 실제 글꼴 이름으로 푼다."""
    if theme is None:
        return None
    group = "majorFont" if ref.startswith("major") else "minorFont"
    tag = "ea" if "EastAsia" in ref else "latin"
    node = theme.find(f"{_A}themeElements/{_A}fontScheme/{_A}{group}/{_A}{tag}")
    return (node.get("typeface") or None) if node is not None else None


_EA_SCRIPTS = {"ko-KR": "Hang", "ja-JP": "Jpan", "zh-CN": "Hans", "zh-TW": "Hant"}


def _localize_theme(
    theme: ElementTree.Element | None, settings: ElementTree.Element | None
) -> ElementTree.Element | None:
    """빈 테마 `a:ea`를 `themeFontLang` 동아시아 언어의 `a:font script` 글꼴로 채운다."""
    lang = _w_val(settings, "themeFontLang", "eastAsia")
    script = _EA_SCRIPTS.get(lang or "")
    if theme is None or script is None:
        return theme
    for group in ("majorFont", "minorFont"):
        node = theme.find(f"{_A}themeElements/{_A}fontScheme/{_A}{group}")
        ea = None if node is None else node.find(f"{_A}ea")
        if node is None or (ea is not None and ea.get("typeface")):
            continue
        for font in node.iter(f"{_A}font"):
            if font.get("script") == script and font.get("typeface"):
                if ea is None:
                    ea = ElementTree.SubElement(node, f"{_A}ea")
                ea.set("typeface", font.get("typeface", ""))
                break
    return theme


def _word_color(rpr: ElementTree.Element | None, theme: ElementTree.Element | None) -> str | None:
    node = None if rpr is None else rpr.find(f"{_W}color")
    if node is None:
        return None
    literal = node.get(f"{_W}val", "")
    theme_name = node.get(f"{_W}themeColor")
    adjusted = node.get(f"{_W}themeShade") or node.get(f"{_W}themeTint")
    if theme_name and not adjusted and (c := _theme_color_hex(theme, theme_name)):
        return c
    if _HEX6.fullmatch(literal):  # shade/tint: the stored literal is already adjusted
        return literal.upper()
    return _theme_color_hex(theme, theme_name) if theme_name else None


def _word_fonts(
    fonts: ElementTree.Element, theme: ElementTree.Element | None
) -> tuple[str | None, str | None]:
    latin = None
    for attr in ("asciiTheme", "hAnsiTheme"):
        if ref := fonts.get(f"{_W}{attr}"):
            latin = _theme_face(theme, ref)
            break
    latin = latin or fonts.get(f"{_W}ascii") or fonts.get(f"{_W}hAnsi")
    east = None
    if ref := fonts.get(f"{_W}eastAsiaTheme"):
        east = _theme_face(theme, ref)
    return latin, east or fonts.get(f"{_W}eastAsia")


def _word_props(
    ppr: ElementTree.Element | None,
    rpr: ElementTree.Element | None,
    theme: ElementTree.Element | None = None,
) -> dict[str, Any]:
    """문단(`w:pPr`)·글자(`w:rPr`) 속성에서 제목 규칙을 읽는다. 테마 참조는 `theme`로 푼다."""
    out: dict[str, Any] = {}
    if jc := _w_val(ppr, "jc"):
        out["title_align"] = _DOCX_ALIGN.get(jc, jc)
    if (sz := _w_val(rpr, "sz")) and sz.isdigit():
        out["title_size"] = int(sz) / 2
    if color := _word_color(rpr, theme):
        out["title_color"] = color
    fonts = None if rpr is None else rpr.find(f"{_W}rFonts")
    if fonts is not None:
        face, east = _word_fonts(fonts, theme)
        if face:
            out["font"] = face
        if east:
            out["font_ea"] = east
    return out


def _style_chain(
    styles: ElementTree.Element, style_id: str, theme: ElementTree.Element | None = None
) -> list[dict[str, Any]]:
    """스타일과 `basedOn` 조상, 마지막으로 `docDefaults`의 속성을 가까운 것부터 돌려준다."""
    by_id = {s.get(f"{_W}styleId", ""): s for s in styles.iter(f"{_W}style")}
    layers: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = style_id
    while current and current in by_id and current not in seen:
        seen.add(current)
        node = by_id[current]
        layers.append(_word_props(node.find(f"{_W}pPr"), node.find(f"{_W}rPr"), theme))
        current = _w_val(node, "basedOn")
    defaults = styles.find(f"{_W}docDefaults")
    if defaults is not None:
        layers.append(
            _word_props(
                defaults.find(f"{_W}pPrDefault/{_W}pPr"),
                defaults.find(f"{_W}rPrDefault/{_W}rPr"),
                theme,
            )
        )
    return layers


def _first_heading_paragraphs(
    document: ElementTree.Element, names: dict[str, str] | None = None
) -> dict[str, ElementTree.Element]:
    found: dict[str, ElementTree.Element] = {}
    for p in document.iter(f"{_W}p"):
        level = paragraph_level(p, names or {})
        if level and level not in found:
            found[level] = p
    return found


def extract_docx_rules(source: Path) -> dict[str, Any]:
    """양식 docx의 제목 1~3 스타일에서 수준별 규칙을 뽑는다.

    결과는 수준 1의 규칙(`title_*`·`font`)에 수준별 규칙 `headings`와 `kind: docx`를 더한 dict다.
    첫 제목 문단의 직접 서식이 있으면 스타일 값보다 우선한다. 못 찾으면 `TemplateError`.
    """
    if not source.is_file():
        raise TemplateError(f"읽을 수 있는 .docx 양식 파일이 아니다: {source}")
    try:
        with zipfile.ZipFile(source) as z:
            styles = ElementTree.fromstring(z.read("word/styles.xml"))
            document = ElementTree.fromstring(z.read("word/document.xml"))
            theme = _read_xml(z, "word/theme/theme1.xml")
            settings = _read_xml(z, "word/settings.xml")
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
        raise TemplateError(f"양식 파일을 읽지 못했다: {exc}") from exc
    theme = _localize_theme(theme, settings)
    names = style_names(styles)
    direct = _first_heading_paragraphs(document, names)
    headings: dict[str, dict[str, Any]] = {}
    for style in styles.iter(f"{_W}style"):
        style_id = style.get(f"{_W}styleId", "")
        level = heading_level(style_id, names.get(style_id, ""))
        if level is None or level in headings:
            continue
        merged: dict[str, Any] = {}
        layers = _style_chain(styles, style_id, theme)
        if level in direct:
            ppr = direct[level].find(f"{_W}pPr")
            run = direct[level].find(f"{_W}r/{_W}rPr")
            layers.insert(0, _word_props(ppr, run, theme))
        for layer in layers:
            for key, value in layer.items():
                merged.setdefault(key, value)
        if merged:
            headings[level] = merged
    if not headings:
        raise TemplateError("양식에서 제목 1~3 서식을 찾지 못했다")
    first = headings[min(headings)]
    return {**first, "headings": dict(sorted(headings.items())), "kind": "docx"}


def register_template(name: str, source: Path | str, home: Path | None = None) -> dict[str, Any]:
    """양식 파일(.pptx 또는 .docx)에서 규칙을 뽑아 `name`으로 저장하고, 저장한 규칙을 돌려준다."""
    target = _path(name, home)
    path = Path(source)
    rules = extract_docx_rules(path) if path.suffix.lower() == ".docx" else extract_rules(path)
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
