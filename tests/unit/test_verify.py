"""verify_numbers 단위 테스트 (FR-08)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from docmill.tools import verify
from docmill.tools.report import Group
from docmill.tools.verify import compare, read_chart_points, verify_numbers

CHART = (
    '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">'
    "<c:chart><c:plotArea><c:barChart><c:ser>"
    "<c:cat><c:strRef><c:strCache>"
    '<c:pt idx="0"><c:v>A</c:v></c:pt><c:pt idx="1"><c:v>B</c:v></c:pt>'
    "</c:strCache></c:strRef></c:cat>"
    "<c:val><c:numRef><c:numCache>"
    '<c:pt idx="0"><c:v>FIRST</c:v></c:pt><c:pt idx="1"><c:v>20</c:v></c:pt>'
    "</c:numCache></c:numRef></c:val>"
    "</c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>"
)

GROUPS = [Group("A", 10.0, 33.3), Group("B", 20.0, 66.7)]


def _pptx(path: Path, first: str) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/charts/chart1.xml", CHART.replace("FIRST", first))
    return path


def test_matching_chart_has_no_mismatches(tmp_path: Path) -> None:
    result = compare(read_chart_points(_pptx(tmp_path / "a.pptx", "10")), GROUPS)
    assert result == {"checked": 2, "matched": 2, "mismatches": []}


def test_wrong_value_reports_location_and_expected(tmp_path: Path) -> None:
    result = compare(read_chart_points(_pptx(tmp_path / "a.pptx", "11")), GROUPS)
    assert result["matched"] == 1
    (bad,) = result["mismatches"]
    assert "chart1" in bad["location"] and "'A'" in bad["location"]
    assert bad["expected"] == 10.0 and bad["actual"] == "11"


def test_unknown_category_is_mismatch(tmp_path: Path) -> None:
    result = compare(read_chart_points(_pptx(tmp_path / "a.pptx", "10")), GROUPS[:1])
    assert result["matched"] == 1
    assert result["mismatches"][0]["expected"] is None


def test_category_without_value_is_mismatch(tmp_path: Path) -> None:
    pptx = tmp_path / "a.pptx"
    with zipfile.ZipFile(pptx, "w") as z:
        z.writestr(
            "ppt/charts/chart1.xml",
            CHART.replace("<c:v>FIRST</c:v>", "<c:v/>").replace(
                '<c:pt idx="1"><c:v>20</c:v></c:pt>', ""
            ),
        )
    result = compare(read_chart_points(pptx), GROUPS)
    assert result["checked"] == 2 and result["matched"] == 0
    assert [m["actual"] for m in result["mismatches"]] == [None, None]


def test_categories_only_chart_reaches_compare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify, "_read_sheet", lambda *_: [])
    monkeypatch.setattr(verify, "aggregate", lambda *_: GROUPS)
    xlsx = tmp_path / "s.xlsx"
    xlsx.write_bytes(b"")
    pptx = tmp_path / "a.pptx"
    with zipfile.ZipFile(pptx, "w") as z:
        z.writestr(
            "ppt/charts/chart1.xml",
            CHART.replace('<c:pt idx="0"><c:v>FIRST</c:v></c:pt>', "").replace(
                '<c:pt idx="1"><c:v>20</c:v></c:pt>', ""
            ),
        )
    out = json.loads(verify_numbers(str(pptx), str(xlsx), "g", "v"))
    assert "error" not in out
    assert out["checked"] == 2 and out["matched"] == 0


def test_invalid_inputs_report_step(tmp_path: Path) -> None:
    out = json.loads(verify_numbers(str(tmp_path / "x.pptx"), str(tmp_path / "y.xlsx"), "g", "v"))
    assert out["step"] == "document" and out["run_id"]
    doc = _pptx(tmp_path / "a.pptx", "10")
    out = json.loads(verify_numbers(str(doc), str(tmp_path / "y.xlsx"), "g", "v"))
    assert out["step"] == "source"


def test_hwp_points_match_group_totals_like_chart() -> None:
    series = verify.read_hwp_points(["주간 보고 2026", "A: 10", "B 20"], GROUPS)
    assert compare(series, GROUPS) == {"checked": 2, "matched": 2, "mismatches": []}
    assert series[0]["ignored"] == ["주간 보고 2026"]


def test_hwp_unknown_label_with_separator_is_mismatch() -> None:
    result = compare(verify.read_hwp_points(["A: 10", "C: 20"], GROUPS), GROUPS)
    assert result["checked"] == 2 and result["matched"] == 1
    assert result["mismatches"] == [{"location": "hwp 계열1 'C'", "expected": None, "actual": "20"}]


@pytest.mark.parametrize(
    ("line", "number"),
    [("A: -10", "-10"), ("A -10", "-10"), ("A = -10", "-10"), ("A - 10", "10"), ("A-10", "10")],
)
def test_hwp_minus_sign_is_kept_and_dash_separator_allowed(line: str, number: str) -> None:
    series = verify.read_hwp_points([line], GROUPS)
    assert series[0]["categories"] == {0: "A"} and series[0]["values"] == {0: number}


def test_hwp_negative_value_matches_only_negative_total() -> None:
    negative = [Group("A", -10.0, 100.0)]
    positive = [Group("A", 10.0, 100.0)]
    lines = ["A: -10"]
    assert compare(verify.read_hwp_points(lines, negative), negative)["matched"] == 1
    mismatch = compare(verify.read_hwp_points(lines, positive), positive)
    assert mismatch["matched"] == 0 and mismatch["mismatches"][0]["actual"] == "-10"


def test_hwp_wrong_value_is_mismatch() -> None:
    result = compare(verify.read_hwp_points(["A: 10", "B: 21"], GROUPS), GROUPS)
    assert result["matched"] == 1 and result["mismatches"][0]["expected"] == 20.0


def test_hwp_same_lines_as_pptx_give_same_result(tmp_path: Path) -> None:
    pptx = compare(read_chart_points(_pptx(tmp_path / "a.pptx", "10")), GROUPS)
    hwp = compare(verify.read_hwp_points(["A: 10", "B: 20"], GROUPS), GROUPS)
    assert pptx == hwp


def test_hwp_input_reaches_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify, "_read_sheet", lambda *_: [])
    monkeypatch.setattr(verify, "aggregate", lambda *_: GROUPS)
    monkeypatch.setattr(verify, "read_hwp_lines", lambda _p: ["A: 10", "B: 20"])
    xlsx, hwp = tmp_path / "s.xlsx", tmp_path / "a.hwpx"
    xlsx.write_bytes(b"")
    hwp.write_bytes(b"x")
    out = json.loads(verify_numbers(str(hwp), str(xlsx), "g", "v"))
    assert out["checked"] == 2 and out["matched"] == 2 and out["ignored"] == []


def test_broken_hwp_is_reported_and_server_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify, "_read_sheet", lambda *_: [])
    monkeypatch.setattr(verify, "aggregate", lambda *_: GROUPS)
    xlsx, hwp = tmp_path / "s.xlsx", tmp_path / "broken.hwp"
    xlsx.write_bytes(b"")
    hwp.write_bytes(b"not a hwp")
    out = json.loads(verify_numbers(str(hwp), str(xlsx), "g", "v"))
    assert out["step"] == "read" and out["run_id"]
