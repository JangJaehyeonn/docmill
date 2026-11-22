"""비슷한 문서 찾기 단위 테스트 (FR-48)."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

from docmill.tools.similar import find_similar_documents, similarity


def _docx(path: Path, first: str) -> None:
    xml = f"<w:document><w:body><w:p><w:r><w:t>{first}</w:t></w:r></w:p></w:body></w:document>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)


def test_similarity_rules() -> None:
    assert similarity("주간 보고", "주간보고") == 1.0
    assert similarity("주간 보고", "회의록") == 0.0
    assert similarity("", "x") == 0.0


def test_recent_first_and_stable(tmp_path: Path) -> None:
    for name, mtime in (("a.docx", 100), ("b.docx", 300), ("c.docx", 200)):
        _docx(tmp_path / name, "주간 보고")
        os.utime(tmp_path / name, (mtime, mtime))
    _docx(tmp_path / "m.docx", "회의록")
    out = find_similar_documents(str(tmp_path), "주간 보고")
    assert [i["path"] for i in json.loads(out)["similar"]] == ["b.docx", "c.docx", "a.docx"]
    assert find_similar_documents(str(tmp_path), "주간 보고") == out
