# syntax=docker/dockerfile:1.7

FROM node:24-alpine AS assets
WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts

COPY scripts/build-js.mjs scripts/copy-fonts.mjs ./scripts/
COPY src/static_src ./src/static_src
COPY src/templates ./src/templates
COPY src/core ./src/core
RUN npm run build


FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/app/.venv/bin:$PATH \
    UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        curl \
        libpq5 \
        tesseract-ocr \
        tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system sheep \
    && useradd --system --gid sheep --create-home --home-dir /home/sheep sheep

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

RUN playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/*
RUN playwright install --only-shell chromium \
    && mv /root/.cache/ms-playwright /ms-playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY src ./src
COPY scripts ./scripts
COPY docs ./docs
RUN uv sync --frozen --no-dev

COPY --from=assets /build/src/static/css ./src/static/css
COPY --from=assets /build/src/static/js/app.js ./src/static/js/app.js

RUN mkdir -p /app/var/static \
    && python src/manage.py collectstatic --noinput \
    && chown -R sheep:sheep /app/var /home/sheep

USER sheep

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:${PORT:-8000}/health/live >/dev/null || exit 1

CMD ["/bin/sh", "-c", "exec gunicorn config.wsgi:application --chdir src --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --threads ${GUNICORN_THREADS:-2} --timeout 120 --access-logfile - --error-logfile -"]
