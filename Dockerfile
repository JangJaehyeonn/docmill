# docmill MCP 서버 이미지 (FR-24)
# 엔진(GridCraft·DeckCraft·WordCraft)을 포함하고, 기본으로 HTTP 모드(포트 8000)를 실행한다.
# 외부에서 접속하려면 DOCMILL_HTTP_TOKEN을 넘겨야 한다. 없으면 컨테이너 안 127.0.0.1에만 바인딩한다.
# stdio로 쓰려면 `docker run -i <이미지> mcp`처럼 명령을 바꾼다.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
RUN uv sync --locked --no-dev

# Engines are installed by `docmill setup` (pinned versions, checksum-verified) and linked into PATH,
# so DOCMILL_HOME stays free for run logs and recipes.
RUN DOCMILL_HOME=/opt/docmill /app/.venv/bin/docmill setup \
    && for name in gridcraft deckcraft wordcraft; do \
         cli="$(find /opt/docmill/engines -type f -name "$name-cli" | sort | head -n1)"; \
         test -n "$cli" && ln -s "$cli" "/usr/local/bin/$name-cli"; \
       done

RUN useradd --create-home docmill && mkdir -p /data && chown docmill /data
USER docmill
ENV DOCMILL_HOME=/data
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]

ENTRYPOINT ["/app/.venv/bin/docmill"]
CMD ["mcp", "--http", "--host", "0.0.0.0", "--port", "8000"]
