FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc curl make ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Пакеты из module.json -> pip_requirements (modules + core_libraries); только на этапе сборки
RUN python scripts/merge_plugin_requirements.py --write requirements-plugins.generated.txt --install

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
    /app/data/autonomy_backups

EXPOSE 8000

ARG APP_MODE=bot
ENV APP_MODE=${APP_MODE}

CMD ["sh", "-c", "if [ \"$APP_MODE\" = \"api\" ]; then python api.py; else python main.py; fi"]
