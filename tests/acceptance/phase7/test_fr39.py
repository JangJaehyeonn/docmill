"""FR-39 개인정보 자동 가림 (mask_text / unmask_text, studio) 인수 테스트 (초안).

필요한 샘플: 없음. docx는 테스트가 직접 만든다 (개인정보는 모두 가짜 값).
가정(구현 방법이 정해지면 맞춰 고친다):
- MCP 도구 `mask_text` 인자: `text`, 선택 `names`(사람이 등록한 이름 목록).
  결과 JSON: `masked`(가린 문장), `placeholders`(자리표 목록; 각 항목은 `placeholder`와 `kind`, 원래 값은 없음).
  자리표는 `[주민번호1]`·`[전화번호1]`·`[이메일1]`·`[계좌번호1]`·`[이름1]` 형식.
- MCP 도구 `unmask_text` 인자: `text`. 대응표는 서버 프로세스 메모리에만 있으므로
  같은 MCP 세션(서버 프로세스 하나)에서 mask_text 다음에 부른다. 결과 JSON: `text`.
- 실행 로그는 DOCMILL_HOME 아래 SQLite 파일(.db·.sqlite·.sqlite3)이다.
- studio: 문서 탭이 열린 채 메시지를 보내면, 에이전트 입력에는 문서 내용이 담기되 개인정보는 자리표로 바뀐 상태다.
  studio 로그는 서버의 stderr다.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
import os
import sys
import textwrap
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import asyncio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

RRN = "900101-1234567"
PHONE = "010-1234-5678"
EMAIL = "hong.gildong@example.com"
ACCOUNT = "110-123-456789"
NAME = "홍길동"
SENTENCE = f"담당자 {NAME}의 주민등록번호는 {RRN}, 전화번호는 {PHONE}, 이메일은 {EMAIL} 입니다."
SECRETS = [RRN, PHONE, EMAIL, "1234567", "hong.gildong"]

_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)

_FAKE = textwrap.dedent(
    """
    import json, sys
    def _read_stdin(timeout=2.0):
        import os, select
        fd = sys.stdin.fileno()
        chunks = []
        while select.select([fd], [], [], timeout)[0]:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks).decode("utf-8", "replace")
    open(sys.argv[1], "w", encoding="utf-8").write(_read_stdin() + " ".join(sys.argv[2:]))
    def emit(o): print(json.dumps(o, ensure_ascii=False), flush=True)
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "확인했어요"}]}})
    emit({"type": "result", "subtype": "success", "result": "확인했어요"})
    """
)

_FR = "FR-39"
pytestmark = pytest.mark.skipif(
    _FR not in {x.strip().upper() for x in os.environ.get("DOCMILL_ACTIVE_FRS", "").split(",")},
    reason=f"{_FR}: 아직 구현 전인 요구사항",
)


def _make_docx(path: Path, paragraphs: list[str]) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", doc)
    return path


async def _call_many(home: Path, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "docmill.cli", "mcp"],
        env={**os.environ, "DOCMILL_HOME": str(home)},
        cwd=str(home),
    )
    outs: list[dict[str, Any]] = []
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        for name, args in calls:
            result = await session.call_tool(name, args)
            outs.append(dict(json.loads("".join(getattr(c, "text", "") for c in result.content))))
    return outs


def _run(home: Path, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return asyncio.run(asyncio.wait_for(_call_many(home, calls), 120))


@pytest.mark.fr("FR-39")
def test_mask_text_removes_every_original_value(tmp_path: Path) -> None:
    (out,) = _run(tmp_path, [("mask_text", {"text": SENTENCE, "names": [NAME]})])
    for secret in [*SECRETS, NAME]:
        assert secret not in out["masked"], secret
    assert "[주민번호1]" in out["masked"] and "[전화번호1]" in out["masked"]
    assert "[이메일1]" in out["masked"]
    assert len(out["placeholders"]) >= 4
    assert all(p["placeholder"] in out["masked"] for p in out["placeholders"])
    dumped = json.dumps(out, ensure_ascii=False)
    assert all(s not in dumped for s in [*SECRETS, NAME]), "결과 어디에도 원래 값이 없어야 한다"


@pytest.mark.fr("FR-39")
def test_unmask_text_restores_original_sentence(tmp_path: Path) -> None:
    async def _session() -> dict[str, Any]:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "docmill.cli", "mcp"],
            env={**os.environ, "DOCMILL_HOME": str(tmp_path)},
            cwd=str(tmp_path),
        )
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("mask_text", {"text": SENTENCE, "names": [NAME]})
            masked = json.loads("".join(getattr(c, "text", "") for c in res.content))
            assert masked["masked"] != SENTENCE
            back = await s.call_tool("unmask_text", {"text": masked["masked"]})
            return dict(json.loads("".join(getattr(c, "text", "") for c in back.content)))

    out = asyncio.run(asyncio.wait_for(_session(), 120))
    assert out["text"] == SENTENCE


@pytest.mark.fr("FR-39")
def test_run_log_has_no_original_values(tmp_path: Path) -> None:
    _run(tmp_path, [("mask_text", {"text": SENTENCE, "names": [NAME]})])
    dbs = [p for p in tmp_path.rglob("*") if p.suffix in {".db", ".sqlite", ".sqlite3"}]
    assert dbs, "실행 로그(SQLite)가 있어야 한다"
    for db in dbs:
        raw = db.read_bytes()
        wal = [Path(str(db) + s) for s in ("-wal", "-journal")]
        raw += b"".join(p.read_bytes() for p in wal if p.exists())
        for secret in [*SECRETS, NAME]:
            assert secret.encode("utf-8") not in raw, f"{db.name}에 {secret}가 남았다"


@pytest.mark.fr("FR-39")
def test_studio_agent_input_has_no_personal_data_and_logs_are_clean(
    start_studio: Callable[..., str], page: Any, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _make_docx(ws / "contact.docx", ["연락처 목록", SENTENCE])
    script = tmp_path / "fake.py"
    script.write_text(_FAKE, encoding="utf-8")
    dump = tmp_path / "input.txt"
    env = {"DOCMILL_STUDIO_AGENT_CMD": f'"{sys.executable}" "{script}" "{dump}"'}
    page.goto(start_studio(workspace=ws, env=env))
    page.get_by_role("listitem").filter(has_text="contact.docx").first.click()
    page.get_by_role("tab", name="contact.docx").wait_for()
    page.get_by_label("메시지").fill("이 문서를 요약해줘")
    page.get_by_role("button", name="보내기", exact=True).click()
    page.locator('[data-role="assistant"]').wait_for()
    seen = dump.read_text(encoding="utf-8")
    assert "연락처 목록" in seen, "문서 내용은 에이전트에 가야 한다"
    for secret in [*SECRETS, NAME]:
        assert secret not in seen, f"에이전트 입력에 {secret}가 있다"
    assert "[주민번호1]" in seen
    captured = capfd.readouterr()
    for secret in [*SECRETS, NAME]:
        assert secret not in captured.err and secret not in captured.out, f"로그에 {secret}가 남았다"
    for p in ws.rglob("*"):
        if p.is_file() and p.name != "contact.docx":
            assert RRN.encode() not in p.read_bytes(), f"{p.name}에 원래 값이 남았다"
