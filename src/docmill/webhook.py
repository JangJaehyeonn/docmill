"""실행 결과 요약을 웹훅으로 전달한다. FR-15."""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

WEBHOOK_ENV = "DOCMILL_WEBHOOK_URL"
TIMEOUT_SECONDS = 10


def send_summary(summary: dict[str, Any]) -> bool:
    """웹훅 URL이 설정돼 있으면 요약을 JSON으로 POST한다.

    어떤 전송 오류도 예외로 올리지 않고 stderr 로그만 남긴다. 성공하면 True를 돌려준다.
    """
    url = os.environ.get(WEBHOOK_ENV, "").strip()
    if not url:
        return False
    try:
        body = json.dumps(summary, ensure_ascii=False, sort_keys=True).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS):
            return True
    except Exception as exc:  # delivery must never fail the run
        logger.warning("웹훅 전송 실패: %s", exc)
        return False
