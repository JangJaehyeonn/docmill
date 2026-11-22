"""`export_document` 도구 (FR-46): 문서를 지정 폴더에 저장하거나 메일 초안(.eml)으로 만든다.

메일은 직접 보내지 않는다. 첨부가 든 `.eml` 파일만 만든다. 같은 입력이면 같은 파일이 나오도록
`Date`·`Message-ID` 헤더를 넣지 않는다 (메일 프로그램이 열 때 채운다).
PDF 변환은 문서 서버가 하므로 studio가 맡는다.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import tempfile
from email.message import EmailMessage
from pathlib import Path

_FOLDER_DEFAULT = "내보내기"


def _fail(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _mail_draft(doc: Path, out_dir: Path, subject: str) -> Path:
    msg = EmailMessage()
    msg["Subject"] = subject or doc.stem
    msg["X-Unsent"] = "1"  # 보내지 않은 초안으로 열린다
    msg.set_content(f"{doc.name} 파일을 첨부합니다.\n")
    ctype, _ = mimetypes.guess_type(doc.name)
    maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
    msg.add_attachment(
        doc.read_bytes(), maintype=maintype, subtype=subtype or "octet-stream", filename=doc.name
    )
    msg.set_boundary("docmill-export-boundary")  # 무작위 경계 대신 고정값: 같은 입력이면 같은 파일
    target = out_dir / f"{doc.stem}.eml"
    # 임시 파일에 쓴 뒤 교체한다. 교체는 링크를 따라가지 않아 링크 대상 외부 파일이 보존된다.
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{target.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(bytes(msg))
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def _inside(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def export_document(
    path: str, kind: str, folder: str = "", subject: str = "", workspace: str | None = None
) -> str:
    """문서를 폴더에 저장(`kind="folder"`)하거나 메일 초안 `.eml`(`kind="mail"`)을 만든다.

    폴더는 작업 폴더 안의 상대 경로여야 하고, 비우면 `내보내기` 폴더를 쓴다.
    메일 초안은 문서 옆에 만든다. 결과는 JSON(`path`, `kind`), 실패는 `error`.
    """
    doc = Path(path)
    if not doc.is_file():
        return _fail("내보낼 문서를 찾지 못했어요.")
    root = Path(workspace).resolve() if workspace else doc.resolve().parent
    if kind == "mail":
        target = _mail_draft(doc, doc.resolve().parent, subject)
    elif kind == "folder":
        dest = (root / (folder.strip() or _FOLDER_DEFAULT)).resolve()
        if Path(folder.strip()).is_absolute() or not _inside(root, dest):
            return _fail("작업 폴더 안의 폴더만 지정할 수 있어요.")
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / doc.name
        # 대상 파일이 작업 폴더 밖을 가리키는 링크이면 쓰지 않는다.
        if not _inside(root, target.resolve()):
            return _fail("작업 폴더 밖의 파일에는 쓸 수 없어요.")
        shutil.copyfile(doc, target)
    else:
        return _fail("내보내기 종류는 mail 또는 folder여야 해요.")
    rel = target.relative_to(root).as_posix() if _inside(root, target) else target.name
    return json.dumps({"kind": kind, "path": rel}, ensure_ascii=False)
