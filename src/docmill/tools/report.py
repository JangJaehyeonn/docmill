"""`excel_to_report_ppt` 업무 도구 (FR-05).

엑셀에서 group_by별 value 합계·비중을 계산해 표지·요약·그래프·상세·마무리 슬라이드가 있는
pptx를 만든다. 계산과 슬라이드 구성은 입력만으로 정해지며(시각·난수 없음), 엔진은
`DocumentTool` 어댑터로만 호출한다.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from docmill.engines import get_tool
from docmill.engines.base import EngineResult
from docmill.runlog import current_run_id, get_home, new_run_id

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


def _detail_lines(groups: list[Group], index: int, parts: int) -> str:
    size = -(-len(groups) // parts)
    chunk = groups[index * size : (index + 1) * size]
    lines = [f"{g.name}: {_fmt(g.total)} ({g.share:.1f}%)" for g in chunk]
    return "\n".join(lines) or "추가 항목 없음"


def _summary_text(groups: list[Group], value: str, group_by: str) -> str:
    total = sum(g.total for g in groups)
    top = max(groups, key=lambda g: (g.total, g.name))
    return "\n".join(
        [
            f"{value} 합계: {_fmt(total)}",
            f"{group_by} 수: {len(groups)}개",
            f"최대: {top.name} {_fmt(top.total)} ({top.share:.1f}%)",
        ]
    )


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
            ("add_slide", {"layout": "titleOnly", "title": title}),
            ("insert_chart", chart),
        ]
    if kind == "detail":
        parts = max(count - 4, 1)
        body = _detail_lines(groups, detail_no, parts)
        return [("add_slide", {"layout": "titleAndContent", "title": "상세", "body": body})]
    return [("add_slide", {"layout": "title", "title": "마무리", "body": "감사합니다"})]


def _read_sheet(source: Path, workdir: Path) -> list[list[Any]]:
    staged = workdir / "input.xlsx"
    shutil.copyfile(source, staged)
    with get_tool(staged, workdir) as grid:
        _engine_json(grid.open(staged), "open_workbook")
        data = _engine_json(grid._run("read_range", {}), "read_range")
    rows = data.get("values")
    return rows if isinstance(rows, list) else []


def _build_deck(groups: list[Group], args: dict[str, Any], target: Path, workdir: Path) -> None:
    params: dict[str, Any] = {"blank": True}
    if args.get("template"):
        params["theme"] = args["template"]
    with get_tool(target, workdir) as deck:
        _engine_json(deck._run("new_presentation", params), "new_presentation")
        detail_no = 0
        for kind in plan_slides(args["slides"]):
            for tool, tool_args in _slide_commands(kind, groups, args, detail_no):
                _engine_json(deck._run(tool, tool_args), tool)
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


def _generate(args: dict[str, Any], run_id: str) -> dict[str, Any]:
    _validate(args)
    with tempfile.TemporaryDirectory(prefix="docmill-report-") as tmp:
        workdir = Path(tmp).resolve()
        groups = aggregate(
            _read_sheet(Path(args["input"]), workdir), args["group_by"], args["value"]
        )
        built = workdir / "report.pptx"
        _build_deck(groups, args, built, workdir)
        out_dir = get_home() / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        final = out_dir / f"{run_id}.pptx"
        verification = verify_report(built, groups) if args["verify"] else None
        if verification is not None and verification["matched"] != verification["checked"]:
            matched, checked = verification["matched"], verification["checked"]
            raise ReportError("verify", f"그래프와 엑셀 합계 불일치: {matched}/{checked}건 일치")
        shutil.copyfile(built, final)
    total = sum(g.total for g in groups)
    summary = (
        f"{args['group_by']} {len(groups)}개, {args['value']} 합계 {_fmt(total)}, "
        f"슬라이드 {args['slides']}장({args['chart']} 그래프)"
    )
    result: dict[str, Any] = {
        "path": str(final),
        "summary": summary,
        "run_id": run_id,
        "groups": [{"name": g.name, "total": g.total, "share": round(g.share, 1)} for g in groups],
    }
    if verification is not None:
        result["verification"] = verification
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
    결과(checked·matched)를 함께 돌려준다. 결과는 JSON 문자열이며, 실패하면 error와
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
