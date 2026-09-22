FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.10.12 AS uv
FROM python:3.12-slim-bookworm AS runtime
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
LABEL org.opencontainers.image.source="https://github.com/v3rm0n/reoui" \
    org.opencontainers.image.licenses="MIT"
COPY pyproject.toml uv.lock ./
COPY LICENSE ./
COPY reoui/ ./reoui/
RUN uv sync --frozen --no-dev --no-cache \
    && groupadd -g 1000 reoui && useradd -u 1000 -g 1000 -M reoui \
    && mkdir /data /cache /recordings && chown reoui:reoui /data /cache
COPY --from=frontend /build/frontend/dist /app/frontend/dist
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    REOUI_ARCHIVE=/recordings REOUI_DATA=/data REOUI_CACHE=/cache REOUI_WEB=/app/frontend/dist
USER reoui
EXPOSE 8090
ENTRYPOINT ["python", "-m", "reoui.cli"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8090"]
