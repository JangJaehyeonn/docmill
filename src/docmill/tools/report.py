"""`excel_to_report_ppt` 업무 도구 (FR-05).

엑셀에서 group_by별 value 합계·비중을 계산해 표지·요약·그래프·상세·마무리 슬라이드가 있는
pptx를 만든다. 계산과 슬라이드 구성은 입력만으로 정해지며(시각·난수 없음), 엔진은
`DocumentTool` 어댑터로만 호출한다.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from docmill.engines import get_tool
from docmill.engines.base import DirectToolCaller, EngineResult
from docmill.inputschema import map_header
from docmill.runlog import current_run_id, get_home, new_run_id, output_stem
from docmill.tools.provenance import (
    ProvenanceError,
    SentenceSpec,
    SheetTable,
    Sources,
    TextNumber,
    collect,
    collect_sentences,
    dump,
    provenance_path,
    range_origin,
)

CHART_TYPES = {"bar": "column", "line": "line", "pie": "pie"}
MAX_SLIDES = 30
_C_NS = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_TOLERANCE = 1e-9


class ReportError(Exception):
    """보고서 생성 단계 하나가 실패했을 때 발생한다. `step`은 실패한 단계 이름이다."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


@dataclass(frozen=True)
class Group:
    """group_by 값 하나의 합계와 비중(%)."""

    name: str
    total: float
    share: float


def _fmt(number: float) -> str:
    return f"{number:,.0f}" if number == int(number) else f"{number:,.2f}"


def _engine_json(result: EngineResult, step: str) -> dict[str, Any]:
    if not result.ok:
        raise ReportError(step, result.message)
    try:
        data = json.loads(result.output) if isinstance(result.output, str) else result.output
    except ValueError as exc:
        raise ReportError(step, f"엔진 응답을 해석하지 못했다: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _to_number(raw: Any, row_no: int, column: str) -> float | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        raise ReportError("aggregate", f"{row_no}행 '{column}' 값이 숫자가 아니다: {raw!r}")
    if isinstance(raw, int | float):
        return float(raw)
    try:
        return float(str(raw).replace(",", "").strip())
    except ValueError:
        raise ReportError(
            "aggregate", f"{row_no}행 '{column}' 값이 숫자가 아니다: {raw!r}"
        ) from None


def aggregate(values: list[list[Any]], group_by: str, value: str) -> list[Group]:
    """첫 행을 머리글로 보고 group_by별 value 합계와 비중을 이름순으로 돌려준다."""
    header = [
        str(cell).strip() if cell is not None else "" for cell in (values[0] if values else [])
    ]
    missing = [name for name in (group_by, value) if name not in header]
    if missing:
        names = ", ".join(f"'{name}'" for name in missing)
        raise ReportError("columns", f"엑셀에 없는 열: {names} (있는 열: {', '.join(header)})")
    g_idx, v_idx = header.index(group_by), header.index(value)
    sums: dict[str, float] = {}
    for row_no, row in enumerate(values[1:], start=2):
        cells = list(row) + [None] * (len(header) - len(row))
        key = cells[g_idx]
        number = _to_number(cells[v_idx], row_no, value)
        if key is None or not str(key).strip() or number is None:
            continue
        name = str(key).strip()
        sums[name] = sums.get(name, 0.0) + number
    if not sums:
        raise ReportError("aggregate", "합계를 낼 데이터 행이 없다")
    total = sum(sums[name] for name in sorted(sums))
    groups = [Group(n, sums[n], sums[n] / total * 100 if total else 0.0) for n in sorted(sums)]
    if not math.isfinite(total) or not all(
        math.isfinite(g.total) and math.isfinite(g.share) for g in groups
    ):
        raise ReportError("aggregate", "합계나 비중이 유한한 숫자가 아니다 (NaN 또는 무한대 포함)")
    return groups


def plan_slides(count: int) -> list[str]:
    """슬라이드 수에 맞는 구성을 돌려준다. 5장이면 표지·요약·그래프·상세·마무리."""
    fixed = ["cover", "summary", "chart", "closing"]
    if count >= 5:
        return fixed[:3] + ["detail"] * (count - 4) + fixed[3:]
    return {2: ["cover", "chart"], 3: ["cover", "summary", "chart"], 4: fixed}[count]


def _detail_chunk(groups: list[Group], index: int, parts: int) -> list[Group]:
    size = -(-len(groups) // parts)
    return groups[index * size : (index + 1) * size]


def _detail_line(group: Group) -> str:
    return f"{group.name}: {_fmt(group.total)} ({group.share:.1f}%)"


def _detail_lines(groups: list[Group], index: int, parts: int) -> str:
    lines = [_detail_line(g) for g in _detail_chunk(groups, index, parts)]
    return "\n".join(lines) or "추가 항목 없음"


def _top(groups: list[Group]) -> Group:
    return max(groups, key=lambda g: (g.total, g.name))


def _summary_lines(groups: list[Group], value: str, group_by: str) -> list[str]:
    total = sum(g.total for g in groups)
    top = _top(groups)
    return [
        f"{value} 합계: {_fmt(total)}",
        f"{group_by} 수: {len(groups)}개",
        f"최대: {top.name} {_fmt(top.total)} ({top.share:.1f}%)",
    ]


def _summary_text(groups: list[Group], value: str, group_by: str) -> str:
    return "\n".join(_summary_lines(groups, value, group_by))


def sentence_specs(
    groups: list[Group], args: dict[str, Any], sources: Sources
) -> dict[int, list[SentenceSpec]]:
    """슬라이드 순번(0부터) → 그 슬라이드의 요약·상세 문장과 근거 셀 범위 (FR-47)."""
    found: dict[int, list[SentenceSpec]] = {}
    parts = max(args["slides"] - 4, 1)
    detail_no = 0
    for index, kind in enumerate(plan_slides(args["slides"])):
        if kind == "summary":
            lines = _summary_lines(groups, args["value"], args["group_by"])
            origins = [
                sources.total(),
                sources.group_count(),
                sources.group_total(_top(groups).name),
            ]
            found[index] = [SentenceSpec(t, s) for t, s in zip(lines, origins, strict=True)]
        elif kind == "detail":
            chunk = _detail_chunk(groups, detail_no, parts)
            found[index] = [
                SentenceSpec(_detail_line(g), sources.group_total(g.name)) for g in chunk
            ]
            detail_no += 1
    return found


def _shown(number: float, digits: int) -> float:
    """문서에 보이는 자릿수로 반올림한 값 (`_fmt`·`:.1f` 표시와 같다)."""
    return float(f"{number:.{digits}f}")


def _group_numbers(kind: str, text: str, group: Group, sources: Sources) -> list[TextNumber]:
    digits = 0 if group.total == int(group.total) else 2
    return [
        TextNumber(
            kind, text, _shown(group.total, digits), sources.group_total(group.name, digits)
        ),
        TextNumber(kind, text, _shown(group.share, 1), sources.group_share(group.name)),
    ]


def text_numbers(
    groups: list[Group], args: dict[str, Any], sources: Sources
) -> dict[int, list[TextNumber]]:
    """슬라이드 순번(0부터) → 그 슬라이드 본문에 보이는 숫자와 출처 (FR-21)."""
    found: dict[int, list[TextNumber]] = {}
    parts = max(args["slides"] - 4, 1)
    detail_no = 0
    for index, kind in enumerate(plan_slides(args["slides"])):
        if kind == "summary":
            body = _summary_text(groups, args["value"], args["group_by"])
            total = sum(g.total for g in groups)
            top = _top(groups)
            digits = 0 if total == int(total) else 2
            found[index] = [
                TextNumber(kind, body, _shown(total, digits), sources.total(digits)),
                TextNumber(kind, body, float(len(groups)), sources.group_count()),
                *_group_numbers(kind, body, top, sources),
            ]
        elif kind == "detail":
            chunk = _detail_chunk(groups, detail_no, parts)
            body = _detail_lines(groups, detail_no, parts)
            found[index] = [n for g in chunk for n in _group_numbers(kind, body, g, sources)]
            detail_no += 1
    return found


def _slide_commands(
    kind: str, groups: list[Group], args: dict[str, Any], detail_no: int
) -> list[tuple[str, dict[str, Any]]]:
    group_by, value, count = args["group_by"], args["value"], args["slides"]
    title = f"{group_by}별 {value}"
    if kind == "cover":
        return [("add_slide", {"layout": "title", "title": f"{title} 보고서"})]
    if kind == "summary":
        body = _summary_text(groups, value, group_by)
        return [("add_slide", {"layout": "titleAndContent", "title": "요약", "body": body})]
    if kind == "chart":
        chart = {
            "type": CHART_TYPES[args["chart"]],
            "title": title,
            "categories": [g.name for g in groups],
            "series": [{"name": value, "values": [g.total for g in groups]}],
        }
        return [
            ("add_slide", {"layout": "blank"}),
            ("insert_chart", chart),
        ]
    if kind == "detail":
        parts = max(count - 4, 1)
        body = _detail_lines(groups, detail_no, parts)
        return [("add_slide", {"layout": "titleAndContent", "title": "상세", "body": body})]
    return [("add_slide", {"layout": "title", "title": "마무리", "body": "감사합니다"})]


def _call(engine: DirectToolCaller, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _engine_json(engine.call_tool(tool, arguments), tool)


def read_table(source: Path, workdir: Path) -> SheetTable:
    """엑셀 첫 시트의 사용 범위를 시트 이름·시작 위치와 함께 읽는다."""
    staged = workdir / "input.xlsx"
    shutil.copyfile(source, staged)
    with get_tool(staged, workdir) as grid:
        _engine_json(grid.open(staged), "open_workbook")
        data = _call(grid, "read_range", {})
    rows = data.get("values")
    first_row, first_col = range_origin(str(data.get("range") or ""))
    return SheetTable(
        file=str(source),
        sheet=str(data.get("sheet") or ""),
        first_row=first_row,
        first_col=first_col,
        values=map_header(rows) if isinstance(rows, list) else [],  # FR-22 column map
    )


def _read_sheet(source: Path, workdir: Path) -> list[list[Any]]:
    return read_table(source, workdir).values


def _build_deck(groups: list[Group], args: dict[str, Any], target: Path, workdir: Path) -> None:
    params: dict[str, Any] = {"blank": True}
    if args.get("template"):
        params["theme"] = args["template"]
    with get_tool(target, workdir) as deck:
        _call(deck, "new_presentation", params)
        detail_no = 0
        for kind in plan_slides(args["slides"]):
            for tool, tool_args in _slide_commands(kind, groups, args, detail_no):
                _call(deck, tool, tool_args)
            detail_no += kind == "detail"
        _engine_json(deck.save(target), "save_presentation")


def _chart_values(pptx: Path) -> list[float]:
    """pptx의 첫 그래프에 캐시된 값 목록을 읽는다. 그래프가 없으면 빈 목록."""
    with zipfile.ZipFile(pptx) as archive:
        names = sorted(
            n for n in archive.namelist() if re.fullmatch(r"ppt/charts/chart\d+\.xml", n)
        )
        if not names:
            return []
        root = ET.fromstring(archive.read(names[0]))
    return [
        float(v.text) for val in root.iter(f"{_C_NS}val") for v in val.iter(f"{_C_NS}v") if v.text
    ]


def verify_report(pptx: Path, groups: list[Group]) -> dict[str, int]:
    """저장한 pptx의 그래프 값을 엑셀 합계와 대조한다. 그룹별 1건 + 총합 1건을 센다."""
    plotted = _chart_values(pptx)
    matched = 0
    for index, group in enumerate(groups):
        if index < len(plotted) and abs(plotted[index] - group.total) <= _TOLERANCE * max(
            1.0, abs(group.total)
        ):
            matched += 1
    expected_total = sum(g.total for g in groups)
    if len(plotted) == len(groups) and abs(sum(plotted) - expected_total) <= 1e-6 * max(
        1.0, abs(expected_total)
    ):
        matched += 1
    return {"checked": len(groups) + 1, "matched": matched}


def _validate(args: dict[str, Any]) -> None:
    if args["chart"] not in CHART_TYPES:
        raise ReportError("arguments", f"chart는 bar·line·pie 중 하나여야 한다: {args['chart']!r}")
    if not 2 <= args["slides"] <= MAX_SLIDES:
        raise ReportError("arguments", f"slides는 2~{MAX_SLIDES} 사이여야 한다: {args['slides']}")
    source = Path(args["input"])
    if source.suffix.lower() != ".xlsx" or not source.is_file():
        raise ReportError("input", f"읽을 수 있는 .xlsx 파일이 아니다: {args['input']}")


def _publish(built: Path, final: Path) -> None:
    """산출물을 임시 파일로 복사한 뒤 원자적으로 교체한다. 실패하면 임시 파일을 지운다."""
    partial = final.with_name(f".{final.name}.part")
    try:
        shutil.copyfile(built, partial)
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _render_preview(built: Path, workdir: Path) -> Path | str:
    """첫 슬라이드 PNG를 만든다. 성공하면 PNG 경로, 실패하면 원인 문장을 돌려준다."""
    out_dir = workdir / "preview"
    try:
        out_dir.mkdir()
        with get_tool(built, workdir) as deck:
            rendered = deck.render(built, out_dir)
    except Exception as exc:  # preview is optional; never fail the report because of it
        return f"{type(exc).__name__}: {exc}"
    if not rendered.ok:
        return rendered.message or "미리보기를 만들지 못했다"
    pngs = sorted(out_dir.glob("*.png"))
    return pngs[0] if pngs else "엔진이 PNG를 만들지 않았다"


def _provenance(built: Path, groups: list[Group], args: dict[str, Any], sources: Sources) -> bytes:
    """만든 pptx의 숫자(FR-21)와 요약·상세 문장(FR-47)마다 문서 위치와 원본 출처를 기록한
    provenance.json 내용."""
    names = [g.name for g in groups]
    plan = plan_slides(args["slides"])
    try:
        numbers = collect(built, plan, text_numbers(groups, args, sources), sources, names)
        sentences = collect_sentences(built, plan, sentence_specs(groups, args, sources))
    except (ProvenanceError, KeyError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ReportError("provenance", f"출처를 기록하지 못했다: {exc}") from exc
    return dump(numbers, sentences)


def _generate(args: dict[str, Any], run_id: str) -> dict[str, Any]:
    _validate(args)
    with tempfile.TemporaryDirectory(prefix="docmill-report-") as tmp:
        workdir = Path(tmp).resolve()
        table = read_table(Path(args["input"]), workdir)
        groups = aggregate(table.values, args["group_by"], args["value"])
        built = workdir / "report.pptx"
        _build_deck(groups, args, built, workdir)
        sources = Sources(table, args["group_by"], args["value"])
        built_prov = provenance_path(built)
        built_prov.write_bytes(_provenance(built, groups, args, sources))
        out_dir = get_home() / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        final = out_dir / f"{output_stem(run_id)}.pptx"
        verification = verify_report(built, groups) if args["verify"] else None
        if verification is not None and verification["matched"] != verification["checked"]:
            matched, checked = verification["matched"], verification["checked"]
            raise ReportError("verify", f"그래프와 엑셀 합계 불일치: {matched}/{checked}건 일치")
        total = sum(g.total for g in groups)
        summary = (
            f"{args['group_by']} {len(groups)}개, {args['value']} 합계 {_fmt(total)}, "
            f"슬라이드 {args['slides']}장({args['chart']} 그래프)"
        )
        result: dict[str, Any] = {
            "path": str(final),
            "summary": summary,
            "run_id": run_id,
            "provenance": str(provenance_path(final)),
            "groups": [
                {"name": g.name, "total": g.total, "share": round(g.share, 1)} for g in groups
            ],
        }
        if verification is not None:
            result["verification"] = verification
        preview = _render_preview(built, workdir)
        _publish(built_prov, provenance_path(final))
        _publish(built, final)  # publish only after everything else succeeded
        if isinstance(preview, Path):
            preview_final = final.with_suffix(".png")
            try:
                _publish(preview, preview_final)
            except Exception as exc:  # preview is optional; keep the report result
                result["preview_error"] = f"{type(exc).__name__}: {exc}"
            else:
                result["preview"] = str(preview_final)
        else:
            result["preview_error"] = preview
    return result


def excel_to_report_ppt(
    input: str,
    group_by: str,
    value: str,
    slides: int = 5,
    chart: str = "bar",
    template: str | None = None,
    verify: bool = True,
) -> str:
    """엑셀의 group_by 열 기준 value 합계·비중으로 그래프가 있는 보고서 pptx를 만든다.

    slides는 슬라이드 수(기본 5: 표지·요약·그래프·상세·마무리), chart는 bar·line·pie,
    template은 DeckCraft 테마 이름(선택)이다. verify가 true면 그래프 값과 엑셀 합계를 대조한
    결과(checked·matched)를 함께 돌려준다. 문서 숫자마다 위치와 원본 출처(시트·셀 범위·
    집계 방식)를 기록한 `<결과 파일 이름>.provenance.json` 경로는 provenance에,
    첫 슬라이드 PNG 경로는 preview에 담기고
    (만들지 못하면 preview_error에 원인), 결과는 JSON 문자열이며, 실패하면 error와
    실패한 단계(step)를 담는다.
    """
    run_id = current_run_id() or new_run_id()
    args = {
        "input": input,
        "group_by": group_by,
        "value": value,
        "slides": slides,
        "chart": chart,
        "template": template,
        "verify": verify,
    }
    try:
        result = _generate(args, run_id)
    except ReportError as exc:
        result = {"error": exc.message, "step": exc.step, "run_id": run_id}
    except Exception as exc:  # engine or file failures must not kill the server
        result = {"error": f"{type(exc).__name__}: {exc}", "step": "unexpected", "run_id": run_id}
    return json.dumps(result, ensure_ascii=False)
