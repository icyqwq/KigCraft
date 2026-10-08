# API, worker and migration image. Build context: ./backend
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# The worker drives one pinned Codex CLI build; V2_CODEX_VERSION must equal this value (docker-compose.yml passes it).
ARG CODEX_VERSION=0.159.0-alpha.12.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl nodejs npm fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --prefix /opt/codex-pkg "@openai/codex@${CODEX_VERSION}" \
    # The native binary and its code-mode host sit side by side; the runtime resolves the symlink to that directory.
    && mkdir -p /opt/codex \
    && ln -s "$(find /opt/codex-pkg -path '*x86_64-unknown-linux-musl/bin/codex' -type f | head -1)" /opt/codex/codex \
    && /opt/codex/codex --version | grep -F "codex-cli ${CODEX_VERSION}" \
    && test -f "$(dirname "$(readlink -f /opt/codex/codex)")/codex-code-mode-host"

COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir .

COPY app /app/app
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
