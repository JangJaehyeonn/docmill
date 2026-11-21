import email
import email.policy
import json
from pathlib import Path

from docmill.tools.export import export_document


def _doc(tmp_path: Path) -> Path:
    doc = tmp_path / "report.docx"
    doc.write_bytes(b"PK\x03\x04binary")
    return doc


def test_mail_draft_attaches_document_and_is_reproducible(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    out = json.loads(export_document(str(doc), "mail"))
    assert out == {"kind": "mail", "path": "report.eml"}
    eml = tmp_path / "report.eml"
    first = eml.read_bytes()
    msg = email.message_from_bytes(first, policy=email.policy.default)
    assert isinstance(msg, email.message.EmailMessage)
    attached = {p.get_filename(): p.get_content() for p in msg.iter_attachments()}
    assert attached == {"report.docx": doc.read_bytes()}
    assert msg["Date"] is None
    export_document(str(doc), "mail")
    assert eml.read_bytes() == first


def test_folder_copy_defaults_and_rejects_escape(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    out = json.loads(export_document(str(doc), "folder", workspace=str(tmp_path)))
    assert out["path"] == "내보내기/report.docx"
    assert (tmp_path / "내보내기" / "report.docx").read_bytes() == doc.read_bytes()
    bad = json.loads(export_document(str(doc), "folder", "../x", workspace=str(tmp_path)))
    assert "error" in bad and not (tmp_path.parent / "x").exists()


def test_unknown_kind_and_missing_file(tmp_path: Path) -> None:
    assert "error" in json.loads(export_document(str(_doc(tmp_path)), "fax"))
    assert "error" in json.loads(export_document(str(tmp_path / "none.docx"), "mail"))
