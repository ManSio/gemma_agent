# syntax=docker/dockerfile:1
# Multi-stage production image (bot or API via APP_MODE).
# Build: docker build -t gemma-bot:latest .
# Native venv on VPS remains the primary path — see docs/DEPLOY.md.

FROM python:3.11-slim-bookworm AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc make \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

COPY . .
RUN python scripts/merge_plugin_requirements.py --write requirements-plugins.generated.txt --install \
    && pip install --no-cache-dir --prefix=/install -r requirements-plugins.generated.txt


FROM python:3.11-slim-bookworm AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 gemma \
    && useradd --uid 1000 --gid gemma --create-home --shell /usr/sbin/nologin gemma

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=gemma:gemma . .

RUN mkdir -p /app/data/rag \
    /app/data/cache \
    /app/data/models \
    /app/data/database \
    /app/data/users \
    /app/data/psychology \
    /app/data/digital_twin \
    /app/data/group_behavior \
    /app/data/security \
    /app/data/books \
    /app/data/schedule \
    /app/data/mem0 \
    /app/data/runtime \
    /app/data/passport_backups \
    /app/data/autonomy_backups \
    && chown -R gemma:gemma /app/data

USER gemma

EXPOSE 8000

ARG APP_MODE=bot
ENV APP_MODE=${APP_MODE}

CMD ["sh", "-c", "if [ \"$APP_MODE\" = \"api\" ]; then python api.py; else python main.py; fi"]
