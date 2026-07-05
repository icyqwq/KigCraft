FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git nodejs npm fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @openai/codex \
    && codex --version

COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir ".[dev]"

COPY app /app/app
COPY tests /app/tests

EXPOSE 8000

