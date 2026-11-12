"""FR-03 공통 엔진 어댑터 인수 테스트 (초안).

필요한 샘플 (tests/fixtures/): sales_q3.xlsx, company_intro.pptx, meeting_1111.docx
가정(구현에 맞춰 고친다):
- `docmill.engines.get_tool(path, workdir=None)`이 확장자(대소문자 무시)로 어댑터를 골라
  돌려준다. 지원하지 않는 확장자는 ValueError.
- 어댑터는 컨텍스트 매니저이고 `.workdir` 속성(작업별 임시 폴더)을 가진다.
  workdir을 주지 않으면 어댑터가 임시 폴더를 만들고 종료 시(오류 포함) 지운다.
- 어댑터 클래스: docmill.engines.gridcraft.GridCraftTool 등 (test_fr02 참고).
- 실제 엔진 실행 파일은 PATH의 gridcraft-cli · deckcraft-cli · wordcraft-cli이며
  DOCMILL_*_PATH 환경 변수로 지정한다.
"""

from __future__ import annotations

import importlib
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

# (모듈, 클래스, 실행 파일, 경로 환경 변수, 샘플)
CASES = [
    ("gridcraft", "GridCraftTool", "gridcraft-cli", "DOCMILL_GRIDCRAFT_PATH", "sales_q3.xlsx"),
    ("deckcraft", "DeckCraftTool", "deckcraft-cli", "DOCMILL_DECKCRAFT_PATH", "company_intro.pptx"),
    ("wordcraft", "WordCraftTool", "wordcraft-cli", "DOCMILL_WORDCRAFT_PATH", "meeting_1111.docx"),
]
METHODS = ("open", "execute", "save", "render")


def _adapter_class(module: str, cls: str) -> Any:
    return getattr(importlib.import_module(f"docmill.engines.{module}"), cls)


@pytest.mark.fr("FR-03")
@pytest.mark.parametrize(("module", "cls"), [(c[0], c[1]) for c in CASES])
def test_adapters_implement_document_tool(module: str, cls: str) -> None:
    adapter = _adapter_class(module, cls)
    for method in METHODS:
        assert callable(getattr(adapter, method, None)), f"{cls}.{method} 없음"
    assert adapter.name
    assert adapter.extensions


@pytest.mark.fr("FR-03")
@pytest.mark.parametrize(
    ("ext", "module"), [(".xlsx", "gridcraft"), (".pptx", "deckcraft"), (".DOCX", "wordcraft")]
)
def test_get_tool_picks_adapter_by_extension(ext: str, module: str, tmp_path: Path) -> None:
    from docmill.engines import get_tool

    cls = next(c[1] for c in CASES if c[0] == module)
    with get_tool(tmp_path / f"a{ext}", workdir=tmp_path) as tool:
        assert isinstance(tool, _adapter_class(module, cls))


@pytest.mark.fr("FR-03")
def test_get_tool_rejects_unknown_extension(tmp_path: Path) -> None:
    from docmill.engines import get_tool

    with pytest.raises(ValueError, match=r"\.txt"):
        get_tool(tmp_path / "a.txt", workdir=tmp_path)


@pytest.mark.fr("FR-03")
@pytest.mark.engine
@pytest.mark.parametrize(("module", "cls", "binary", "env", "sample"), CASES)
def test_temp_folder_removed_after_work(
    module: str,
    cls: str,
    binary: str,
    env: str,
    sample: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docmill.engines import get_tool

    exe = shutil.which(binary)
    if exe is None:
        pytest.fail(f"{binary} 실행 파일이 없다 (CI는 setup-engines로 설치한다)")
    monkeypatch.setenv(env, exe)
    with get_tool(Path(sample)) as tool:
        workdir = Path(tool.workdir)
        assert workdir.is_dir()
        shutil.copy(FIXTURES / sample, workdir / sample)
        assert tool.open(workdir / sample).ok
    assert not workdir.exists(), "작업이 끝났는데 임시 폴더가 남았다"


@pytest.mark.fr("FR-03")
def test_temp_folder_removed_even_on_error() -> None:
    from docmill.engines import get_tool

    workdir: Path | None = None
    with pytest.raises(RuntimeError), get_tool(Path("a.xlsx")) as tool:
        workdir = Path(tool.workdir)
        raise RuntimeError("작업 중 오류")
    assert workdir is not None and not workdir.exists()


def _normalize_docx(data: bytes, tmp: Path) -> dict[str, bytes]:
    """dcterms:created·modified 두 시각만 고정값으로 바꾼 파트 목록을 돌려준다."""
    pattern = re.compile(
        rb"(<dcterms:(created|modified)[^>]*>)[^<]*(</dcterms:(?:created|modified)>)"
    )
    path = tmp / "normalize.docx"
    path.write_bytes(data)
    with zipfile.ZipFile(path) as zf:
        parts = {name: zf.read(name) for name in zf.namelist()}
    parts["docProps/core.xml"] = pattern.sub(
        rb"\g<1>2000-01-01T00:00:00Z\g<3>", parts["docProps/core.xml"]
    )
    return parts


@pytest.mark.fr("FR-03")
@pytest.mark.engine
@pytest.mark.parametrize(("module", "cls", "binary", "env", "sample"), CASES)
def test_saving_twice_is_byte_identical(
    module: str,
    cls: str,
    binary: str,
    env: str,
    sample: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docmill.engines import get_tool

    exe = shutil.which(binary)
    if exe is None:
        pytest.fail(f"{binary} 실행 파일이 없다 (CI는 setup-engines로 설치한다)")
    monkeypatch.setenv(env, exe)
    outputs: list[bytes] = []
    for i in range(2):
        with get_tool(Path(sample)) as tool:
            workdir = Path(tool.workdir)
            shutil.copy(FIXTURES / sample, workdir / sample)
            assert tool.open(workdir / sample).ok
            out = workdir / f"out{i}{Path(sample).suffix}"
            assert tool.save(out).ok
            outputs.append(out.read_bytes())
    if sample.endswith(".docx"):
        assert _normalize_docx(outputs[0], tmp_path) == _normalize_docx(outputs[1], tmp_path)
    else:
        assert outputs[0] == outputs[1]
