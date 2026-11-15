"""excel_to_report_ppt 단위 테스트 (FR-05)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docmill.tools.report import (
    Group,
    ReportError,
    aggregate,
    excel_to_report_ppt,
    plan_slides,
    verify_report,
)

ROWS = [
    ["부서", "월", "매출"],
    ["A", "7월", 10],
    ["B", "7월", 30],
    ["A", "8월", 20.5],
    [None, "x", 1],
]


def test_aggregate_sums_and_shares_sorted() -> None:
    groups = aggregate(ROWS, "부서", "매출")
    assert [(g.name, g.total) for g in groups] == [("A", 30.5), ("B", 30.0)]
    assert sum(g.share for g in groups) == pytest.approx(100)


@pytest.mark.parametrize(
    ("group_by", "value", "missing"), [("없음", "매출", "없음"), ("부서", "금액", "금액")]
)
def test_aggregate_reports_missing_column(group_by: str, value: str, missing: str) -> None:
    with pytest.raises(ReportError, match=missing) as info:
        aggregate(ROWS, group_by, value)
    assert info.value.step == "columns"


def test_aggregate_rejects_non_numeric_value() -> None:
    with pytest.raises(ReportError, match="2행"):
        aggregate([["g", "v"], ["a", "abc"]], "g", "v")


def test_aggregate_rejects_non_finite_values() -> None:
    with pytest.raises(ReportError, match="유한"):
        aggregate([["g", "v"], ["a", "inf"]], "g", "v")


@pytest.mark.parametrize("count", range(2, 9))
def test_plan_slides_length_matches(count: int) -> None:
    plan = plan_slides(count)
    assert len(plan) == count
    assert plan[0] == "cover"
    assert "chart" in plan


def test_slides_below_two_rejected() -> None:
    result = json.loads(excel_to_report_ppt("nope.xlsx", "g", "v", slides=1))
    assert result["step"] == "arguments"


def test_plan_five_slides_order() -> None:
    assert plan_slides(5) == ["cover", "summary", "chart", "detail", "closing"]


def test_verify_report_without_chart_matches_nothing(tmp_path: Path) -> None:
    import zipfile

    pptx = tmp_path / "x.pptx"
    with zipfile.ZipFile(pptx, "w") as z:
        z.writestr("ppt/slides/slide1.xml", "<a/>")
    assert verify_report(pptx, [Group("A", 1.0, 100.0)]) == {"checked": 2, "matched": 0}


def test_verification_mismatch_blocks_publishing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import docmill.tools.report as report

    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    source = tmp_path / "in.xlsx"
    source.write_bytes(b"x")
    monkeypatch.setattr(report, "_read_sheet", lambda *_a: ROWS)
    monkeypatch.setattr(report, "_build_deck", lambda _g, _a, built, _w: built.write_bytes(b"p"))
    monkeypatch.setattr(report, "verify_report", lambda *_a: {"checked": 3, "matched": 0})
    out = json.loads(excel_to_report_ppt(str(source), "부서", "매출"))
    assert out["step"] == "verify"
    assert not list((tmp_path / "outputs").glob("*.pptx"))


def test_failure_before_publish_leaves_no_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import docmill.tools.report as report

    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    source = tmp_path / "in.xlsx"
    source.write_bytes(b"x")
    monkeypatch.setattr(report, "_read_sheet", lambda *_a: ROWS)
    monkeypatch.setattr(report, "_build_deck", lambda _g, _a, built, _w: built.write_bytes(b"p"))

    def boom(_number: float) -> str:
        raise RuntimeError("summary failed")

    monkeypatch.setattr(report, "_fmt", boom)
    out = json.loads(excel_to_report_ppt(str(source), "부서", "매출", verify=False))
    assert out["step"] == "unexpected"
    assert not list((tmp_path / "outputs").glob("*.pptx"))


def test_copy_failure_leaves_no_partial_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import docmill.tools.report as report

    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    source = tmp_path / "in.xlsx"
    source.write_bytes(b"x")
    monkeypatch.setattr(report, "_read_sheet", lambda *_a: ROWS)
    monkeypatch.setattr(report, "_build_deck", lambda _g, _a, built, _w: built.write_bytes(b"p"))

    def partial_copy(_src: Path, dst: Path) -> None:
        Path(dst).write_bytes(b"par")
        raise OSError("disk full")

    monkeypatch.setattr(report.shutil, "copyfile", partial_copy)
    out = json.loads(excel_to_report_ppt(str(source), "부서", "매출", verify=False))
    assert "error" in out
    assert not [p for p in (tmp_path / "outputs").iterdir()]


@pytest.mark.parametrize(
    ("overrides", "step"),
    [
        ({"chart": "radar"}, "arguments"),
        ({"slides": 0}, "arguments"),
        ({"input": "nope.xlsx"}, "input"),
    ],
)
def test_invalid_arguments_return_error(
    overrides: dict[str, object], step: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    args: dict[str, object] = {"input": "nope.xlsx", "group_by": "a", "value": "b"}
    args.update(overrides)
    out = json.loads(excel_to_report_ppt(**args))  # type: ignore[arg-type]
    assert out["step"] == step
    assert out["error"]
    assert out["run_id"]
