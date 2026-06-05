# Конфигурация

Главный файл: **`.env`** в корне (не в git).

Полный каталог: `.env.example` + `config/env_catalog/`

## Обязательные секреты

| Переменная | Откуда |
|------------|--------|
| `TELEGRAM_TOKEN` | @BotFather |
| `OPENROUTER_API_KEY` | openrouter.ai |
| `ADMIN_USER_IDS` | Числовой id в Telegram |
| `OWNER_TELEGRAM_ID` | Тот же id владельца |

## Доступ

```env
USER_ACCESS_APPROVAL_REQUIRED=true
```

`false` — бот открыт всем по ссылке (осознанный риск).

## LLM

```env
OPENROUTER_MODEL_FREE=google/gemini-2.0-flash-001:free
OPENROUTER_MODEL_DEV=anthropic/claude-sonnet-4
```

## Поиск и память

```env
SEARXNG_ENABLED=true
SEARXNG_INSTANCE_URL=http://127.0.0.1:8080
MEM0_LOCAL=true
MEM0_API_URL=http://127.0.0.1:8001
```

## Telegram

```env
TELEGRAM_PIPELINE_PRIVATE_PARALLEL=1
TELEGRAM_REPLY_MODE_FOOTER=admin
WEBHOOK_URL=
```

Пустой webhook → polling.

## Голос (опционально)

```env
VOICE_TTS_ENABLED=true
VOICE_TTS_MODEL_PATH=./models/piper/ru_RU-irina-medium.onnx
```

## Панель

`scripts/gemma_panel.local.conf` — пути и `GEMMA_MEM0_USE_STUB=true`.

Справочник: [environment-variables](../reference/environment-variables.md)
