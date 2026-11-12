# docmill MCP 서버 이미지
# 문서 엔진 바이너리 설치는 Phase 0 검증 후 `docmill setup`(FR-04)에서 추가한다.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
RUN uv sync --locked --no-dev

RUN useradd --create-home docmill
USER docmill

ENTRYPOINT ["/app/.venv/bin/docmill"]
CMD ["mcp"]
