"""원격 HTTP 모드 운영 기능 단위 테스트 (FR-24)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from docmill import remote
from docmill.remote import BearerTokenMiddleware, bind_host, engine_status, http_token

ROOT = Path(__file__).resolve().parents[2]
ENGINE_VARS = ("DOCMILL_GRIDCRAFT_PATH", "DOCMILL_DECKCRAFT_PATH", "DOCMILL_WORDCRAFT_PATH")


def _fake_engines(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str = "") -> None:
    for var in ENGINE_VARS:
        exe = tmp_path / var.lower()
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        monkeypatch.setenv(var, str(exe))
    if missing:
        monkeypatch.setenv(missing, str(tmp_path / "nonexistent"))


def test_http_token_ignores_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCMILL_HTTP_TOKEN", "  ")
    assert http_token() is None
    monkeypatch.setenv("DOCMILL_HTTP_TOKEN", "abc")
    assert http_token() == "abc"
    monkeypatch.delenv("DOCMILL_HTTP_TOKEN")
    assert http_token() is None


def test_bind_host_forces_loopback_without_token() -> None:
    assert bind_host("0.0.0.0", None) == "127.0.0.1"
    assert bind_host("127.0.0.1", None) == "127.0.0.1"
    assert bind_host("0.0.0.0", "secret") == "0.0.0.0"


def test_engine_status_reports_each_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_engines(monkeypatch, tmp_path, missing="DOCMILL_DECKCRAFT_PATH")
    status = engine_status()
    assert list(status) == ["gridcraft", "deckcraft", "wordcraft"]
    assert status == {"gridcraft": True, "deckcraft": False, "wordcraft": True}


def _app_with_healthz() -> Starlette:
    return Starlette(routes=[Route("/healthz", remote.healthz, methods=["GET"])])


def test_healthz_ok_when_all_engines_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_engines(monkeypatch, tmp_path)
    resp = TestClient(_app_with_healthz()).get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["engines"] == {"gridcraft": True, "deckcraft": True, "wordcraft": True}


def test_healthz_503_when_engine_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_engines(monkeypatch, tmp_path, missing="DOCMILL_WORDCRAFT_PATH")
    resp = TestClient(_app_with_healthz()).get("/healthz")
    assert resp.status_code == 503
    assert resp.json()["engines"]["wordcraft"] is False


async def _hello(request: Request) -> Response:
    return PlainTextResponse("hi")


def _guarded() -> TestClient:
    inner = Starlette(routes=[Route("/mcp", _hello), Route("/healthz", _hello)])
    return TestClient(BearerTokenMiddleware(inner, "s3cret", public_paths=("/healthz",)))


@pytest.mark.parametrize("header", [None, "Bearer wrong", "s3cret", "Basic s3cret"])
def test_bearer_middleware_rejects_bad_token(header: str | None) -> None:
    headers = {"Authorization": header} if header else {}
    resp = _guarded().get("/mcp", headers=headers)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_bearer_middleware_accepts_token_and_public_path() -> None:
    client = _guarded()
    assert client.get("/mcp", headers={"Authorization": "Bearer s3cret"}).text == "hi"
    assert client.get("/healthz").status_code == 200


def test_bearer_scheme_is_case_insensitive() -> None:
    client = _guarded()
    assert client.get("/mcp", headers={"Authorization": "bearer s3cret"}).text == "hi"
    assert client.get("/mcp", headers={"Authorization": "BEARER s3cret"}).text == "hi"
    assert client.get("/mcp", headers={"Authorization": "bearer wrong"}).status_code == 401


def test_build_http_app_wraps_only_with_token() -> None:
    from docmill.server import build_server

    assert isinstance(remote.build_http_app(build_server(), "0.0.0.0", "t"), BearerTokenMiddleware)
    assert not isinstance(
        remote.build_http_app(build_server(), "127.0.0.1", None), BearerTokenMiddleware
    )


def test_serve_http_binds_loopback_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    from docmill.server import build_server

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(kw))
    monkeypatch.delenv("DOCMILL_HTTP_TOKEN", raising=False)
    remote.serve_http(build_server(), "0.0.0.0", 9001)
    monkeypatch.setenv("DOCMILL_HTTP_TOKEN", "t")
    remote.serve_http(build_server(), "0.0.0.0", 9002)
    assert [(c["host"], c["port"]) for c in calls] == [("127.0.0.1", 9001), ("0.0.0.0", 9002)]


def _deploy(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "deploy" / "deploy.sh"), *args],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    "args", [[], ["--image"], ["--image", "x", "--timeout", "soon"], ["--bogus"]]
)
def test_deploy_script_usage_errors(args: list[str], tmp_path: Path) -> None:
    assert _deploy(args, tmp_path).returncode == 2
