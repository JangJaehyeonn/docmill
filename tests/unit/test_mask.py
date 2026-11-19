import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from docmill.runlog import logged
from docmill.tools import mask
from docmill.tools.mask import MaskTable, detect_names, mask_documents, unmask_document

SENTENCE = (
    "담당자 홍길동의 주민번호 900101-1234567, 전화 010-1234-5678, 메일 a.b@example.com 입니다."
)


def test_mask_removes_values_and_unmask_restores() -> None:
    table = MaskTable()
    masked, used = table.mask(SENTENCE, ["홍길동"])
    for secret in ["900101-1234567", "010-1234-5678", "a.b@example.com", "홍길동"]:
        assert secret not in masked
    assert "[주민번호1]" in masked and "[전화번호1]" in masked and "[이메일1]" in masked
    assert {u["kind"] for u in used} == {"주민번호", "전화번호", "이메일", "이름"}
    assert table.unmask(masked) == SENTENCE


def test_same_value_same_placeholder_and_numbering_is_stable() -> None:
    masked, _ = MaskTable().mask("010-1111-2222 와 010-3333-4444 와 010-1111-2222", None)
    assert masked == "[전화번호1] 와 [전화번호2] 와 [전화번호1]"


def test_unknown_placeholder_is_left_alone() -> None:
    assert MaskTable().unmask("[이름9]") == "[이름9]"


def test_tools_roundtrip_in_one_process() -> None:
    out = json.loads(mask.mask_text(SENTENCE, ["홍길동"]))
    assert "900101" not in json.dumps(out, ensure_ascii=False)
    assert json.loads(mask.unmask_text(out["masked"]))["text"] == SENTENCE


def test_detect_names_strips_particle() -> None:
    assert detect_names("담당자 홍길동의 연락처, 작성자: 김영희") == ["홍길동", "김영희"]


def test_run_log_keeps_no_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    out = json.loads(logged(mask.mask_text)(SENTENCE, ["홍길동"]))
    logged(mask.unmask_text)(out["masked"])
    con = sqlite3.connect(tmp_path / "docmill.db")
    all_rows = con.execute("SELECT * FROM tool_runs").fetchall()
    con.close()
    for tool in ("mask_text", "unmask_text"):
        mine = [r for r in all_rows if tool in map(str, r)]
        assert len(mine) == 1, tool
        row = " ".join(map(str, mine[0]))
        for secret in ["900101", "010-1234", "example.com", "홍길동"]:
            assert secret not in row


def test_existing_placeholder_is_not_reissued() -> None:
    table = MaskTable()
    masked, _ = table.mask("[이름1] / 홍길동", ["홍길동"])
    assert masked == "[이름1] / [이름2]"
    assert table.unmask(masked) == "[이름1] / 홍길동"


def test_repeated_values_share_one_placeholder() -> None:
    text = " ".join(["010-1234-5678"] * 200)
    masked, used = MaskTable().mask(text)
    assert masked == " ".join(["[전화번호1]"] * 200)
    assert len(used) == 1


def test_name_on_length_boundary_is_masked_before_truncation(tmp_path: Path) -> None:
    doc = tmp_path / "a.docx"
    _docx(doc, "가" * (mask.MAX_DOC_CHARS - 2) + "홍길동님")
    out = mask.mask_documents({"paths": [str(doc)], "names": ["홍길동님"]})
    assert "홍길동" not in out["documents"][0]["text"]


def _docx(path: Path, text: str) -> None:
    doc = f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)


def test_mask_documents_and_unmask_document(tmp_path: Path) -> None:
    docx = tmp_path / "a.docx"
    _docx(docx, SENTENCE)
    result = mask_documents({"paths": [str(docx)], "message": "010-1234-5678 확인"})
    text = result["documents"][0]["text"]
    assert "900101" not in text and "홍길동" not in text
    assert result["message"] == "[전화번호1] 확인"
    out = tmp_path / "b.docx"
    _docx(out, text)
    assert unmask_document(out, result["table"]) == 4
    assert SENTENCE in mask.read_document_text(out)
    assert unmask_document(out, result["table"]) == 0
