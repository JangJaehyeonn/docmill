"""verify_numbers 단위 테스트 (FR-08)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

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


def test_invalid_inputs_report_step(tmp_path: Path) -> None:
    out = json.loads(verify_numbers(str(tmp_path / "x.pptx"), str(tmp_path / "y.xlsx"), "g", "v"))
    assert out["step"] == "document" and out["run_id"]
    doc = _pptx(tmp_path / "a.pptx", "10")
    out = json.loads(verify_numbers(str(doc), str(tmp_path / "y.xlsx"), "g", "v"))
    assert out["step"] == "source"
