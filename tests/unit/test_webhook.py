import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from docmill.webhook import send_summary


def _server(status: int, got: list[bytes]) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            got.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(status)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_no_url_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCMILL_WEBHOOK_URL", raising=False)
    assert send_summary({"recipe": "r"}) is False


def test_posts_json_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    got: list[bytes] = []
    server = _server(200, got)
    monkeypatch.setenv("DOCMILL_WEBHOOK_URL", f"http://127.0.0.1:{server.server_port}/h")
    try:
        assert send_summary({"recipe": "주간보고"}) is True
    finally:
        server.shutdown()
    assert json.loads(got[0]) == {"recipe": "주간보고"}


def test_http_error_and_dead_server_do_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    got: list[bytes] = []
    server = _server(500, got)
    monkeypatch.setenv("DOCMILL_WEBHOOK_URL", f"http://127.0.0.1:{server.server_port}/h")
    try:
        assert send_summary({"recipe": "r"}) is False
    finally:
        server.shutdown()
    monkeypatch.setenv("DOCMILL_WEBHOOK_URL", "http://127.0.0.1:9/h")
    assert send_summary({"recipe": "r"}) is False
