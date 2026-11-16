"""엔진 실행 파일 설치 (FR-04).

고정된 버전의 엔진 릴리스를 `DOCMILL_HOME/engines/<이름>-<버전>/`에 내려받아 풀고,
릴리스의 SHA256SUMS.txt로 확인한 뒤 버전을 실행해 본다. 이미 설치되어 있으면 내려받지 않는다.
다운로드는 표준 프록시 환경 변수(HTTPS_PROXY 등)를 따른다.
"""

from __future__ import annotations

import hashlib
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from docmill.runlog import get_home

DOWNLOAD_TIMEOUT_SECONDS = 120
VERSION_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class EngineSpec:
    """설치할 엔진 하나: 표시 이름, 릴리스 저장소 이름, 고정 버전, 버전 확인 인자."""

    display: str
    name: str
    version: str
    version_args: tuple[str, ...]

    @property
    def binary(self) -> str:
        """실행 파일 이름."""
        return f"{self.name}-cli"


# 엔진 버전을 올리면 .github/actions/setup-engines와 함께 바꾼다 (docs/adr/0014).
ENGINES: tuple[EngineSpec, ...] = (
    EngineSpec("GridCraft", "gridcraft", "0.9.0", ("version",)),
    EngineSpec("DeckCraft", "deckcraft", "0.9.0", ("version",)),
    EngineSpec("WordCraft", "wordcraft", "0.8.0", ("--version",)),
)


class EngineInstallError(RuntimeError):
    """엔진을 내려받거나 확인하지 못했을 때 발생한다."""


def engines_dir() -> Path:
    """엔진 설치 기준 폴더."""
    return get_home() / "engines"


def installed_binary(spec: EngineSpec) -> Path | None:
    """설치된 엔진 실행 파일 경로. 없으면 `None`."""
    return _find_binary(engines_dir() / f"{spec.name}-{spec.version}", spec)


def _find_binary(folder: Path, spec: EngineSpec) -> Path | None:
    if not folder.is_dir():
        return None
    found = sorted(p for p in folder.rglob(spec.binary) if p.is_file())
    return found[0] if found else None


def find_installed(binary: str) -> str | None:
    """`gridcraft-cli` 같은 실행 파일 이름으로 설치된 엔진 경로를 찾는다."""
    for spec in ENGINES:
        if spec.binary == binary:
            path = installed_binary(spec)
            return str(path.resolve()) if path else None
    return None


def _asset_name(spec: EngineSpec) -> str:
    machine = platform.machine().lower()
    if platform.system() != "Linux" or machine not in ("x86_64", "amd64"):
        raise EngineInstallError(
            f"이 플랫폼({platform.system()} {machine})용 엔진 내려받기는 아직 지원하지 않는다. "
            f"DOCMILL_{spec.name.upper()}_PATH 환경 변수로 직접 설치한 엔진을 지정한다."
        )
    return f"{spec.name}-{spec.version}-linux-x86_64.tar.gz"


def _fetch(url: str, dest: Path) -> None:
    try:
        with (
            urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp,
            dest.open("wb") as out,
        ):
            shutil.copyfileobj(resp, out)
    except OSError as exc:
        raise EngineInstallError(f"내려받기 실패: {url} ({exc})") from exc


def _verify_checksum(archive: Path, sums: Path) -> None:
    expected = None
    for line in sums.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == archive.name:
            expected = parts[0].lower()
    if expected is None:
        raise EngineInstallError(f"SHA256SUMS에 {archive.name}이 없다")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected:
        raise EngineInstallError(f"체크섬이 맞지 않는다: {archive.name}")


def _check_members(tar: tarfile.TarFile, dest: Path) -> None:
    root = dest.resolve()
    for member in tar.getmembers():
        target = (root / member.name).resolve()
        if not target.is_relative_to(root) or not (member.isfile() or member.isdir()):
            raise EngineInstallError(f"허용하지 않는 압축 항목: {member.name}")


def _extract(archive: Path, dest: Path) -> None:
    try:
        with tarfile.open(archive) as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(dest, filter="data")
            else:  # Python < 3.11.4 has no extraction filters; check members by hand
                _check_members(tar, dest)
                tar.extractall(dest)  # members validated above
    except (tarfile.TarError, OSError) as exc:
        raise EngineInstallError(f"압축 해제 실패: {archive.name} ({exc})") from exc


def _download(spec: EngineSpec) -> Path:
    """엔진을 내려받아 설치 폴더에 풀고 실행 파일 경로를 돌려준다."""
    asset = _asset_name(spec)
    base = f"https://github.com/storytold/{spec.name}/releases/download/v{spec.version}"
    root = engines_dir()
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{spec.name}-{spec.version}"
    with tempfile.TemporaryDirectory(dir=root) as tmp_name:
        tmp = Path(tmp_name)
        archive = tmp / asset
        sums = tmp / "SHA256SUMS.txt"
        _fetch(f"{base}/{asset}", archive)
        _fetch(f"{base}/SHA256SUMS.txt", sums)
        _verify_checksum(archive, sums)
        staging = tmp / "out"
        staging.mkdir()
        _extract(archive, staging)
        staged = _find_binary(staging, spec)
        if staged is None:
            raise EngineInstallError(f"{spec.binary}를 설치 파일에서 찾지 못했다")
        staged.chmod(staged.stat().st_mode | 0o111)
        engine_version(spec, staged)  # reject before it becomes discoverable
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(staging), str(target))
    binary = installed_binary(spec)
    if binary is None:
        raise EngineInstallError(f"{spec.binary}를 설치 파일에서 찾지 못했다")
    return binary


def engine_version(spec: EngineSpec, binary: Path) -> str:
    """엔진을 실행해 버전 출력(예: `gridcraft-cli 0.9.0`)을 돌려준다."""
    try:
        proc = subprocess.run(
            [str(binary), *spec.version_args],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EngineInstallError(f"{spec.display} 버전을 확인하지 못했다: {exc}") from exc
    output = proc.stdout.strip()
    tokens = {t.removeprefix("v") for t in re.split(r"\s+", output)}
    if proc.returncode != 0 or spec.version not in tokens:
        raise EngineInstallError(
            f"{spec.display} 버전이 맞지 않는다 (기대 {spec.version}, 출력 '{output}')"
        )
    return output


def ensure_installed(spec: EngineSpec) -> tuple[str, bool]:
    """엔진을 설치하고 `(버전 출력, 새로 내려받았는지)`를 돌려준다."""
    binary = installed_binary(spec)
    downloaded = False
    if binary is None:
        binary = _download(spec)
        downloaded = True
    return engine_version(spec, binary), downloaded
