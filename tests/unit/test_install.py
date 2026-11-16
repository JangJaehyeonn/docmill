from pathlib import Path

import pytest
from typer.testing import CliRunner

from docmill.cli import app
from docmill.engines import install
from docmill.engines.install import ENGINES, EngineInstallError

runner = CliRunner()


def _fake_binary(spec: install.EngineSpec) -> Path:
    folder = install.engines_dir() / f"{spec.name}-{spec.version}" / "bin"
    folder.mkdir(parents=True)
    path = folder / spec.binary
    path.write_text(f"#!/bin/sh\necho {spec.binary} {spec.version}\n")
    path.chmod(0o755)
    return path


def test_installed_engine_is_not_downloaded_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    for spec in ENGINES:
        _fake_binary(spec)

    def boom(spec: install.EngineSpec) -> Path:
        raise AssertionError("다시 내려받으면 안 된다")

    monkeypatch.setattr(install, "_download", boom)
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0
    for spec in ENGINES:
        assert f"{spec.display}: {spec.binary} {spec.version}" in result.stdout


def test_setup_reports_failure_per_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))

    def fail(spec: install.EngineSpec) -> Path:
        raise EngineInstallError("네트워크 없음")

    monkeypatch.setattr(install, "_download", fail)
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 1
    assert "GridCraft" in result.output and "네트워크 없음" in result.output


def test_version_mismatch_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    spec = ENGINES[0]
    path = _fake_binary(spec)
    path.write_text("#!/bin/sh\necho gridcraft-cli 0.1.0\n")
    with pytest.raises(EngineInstallError):
        install.ensure_installed(spec)


def test_checksum_mismatch_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(b"data")
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text(f"{'0' * 64}  a.tar.gz\n")
    with pytest.raises(EngineInstallError):
        install._verify_checksum(archive, sums)


def test_find_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    assert install.find_installed("gridcraft-cli") is None
    path = _fake_binary(ENGINES[0])
    assert install.find_installed("gridcraft-cli") == str(path)


def test_version_must_match_exactly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))
    spec = ENGINES[0]
    path = _fake_binary(spec)
    path.write_text(f"#!/bin/sh\necho gridcraft-cli {spec.version}1\n")
    with pytest.raises(EngineInstallError):
        install.ensure_installed(spec)


def test_find_installed_returns_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOCMILL_HOME", "rel-home")
    _fake_binary(ENGINES[0])
    assert Path(str(install.find_installed("gridcraft-cli"))).is_absolute()


def test_setup_continues_after_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HOME", str(tmp_path))

    def fail(spec: install.EngineSpec) -> Path:
        raise PermissionError("denied")

    monkeypatch.setattr(install, "_download", fail)
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 1
    assert all(spec.display in result.output for spec in ENGINES)
