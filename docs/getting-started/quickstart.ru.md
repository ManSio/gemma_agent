# Быстрый старт

От нуля до ответа в Telegram за ~15 минут.

## Кому подходит

- Первый деплой на Linux VPS / LAN
- Smoke на Windows (`python main.py`)
- Есть токен Telegram и ключ OpenRouter

## Краткий путь

| Цель | Действие |
|------|----------|
| Установка | `bash scripts/agent_bootstrap.sh` → `.env` → `bash scripts/gemma_panel.sh start-all` |
| Проверка | `python scripts/gemma_status.py --online` |
| TG | «привет» боту |

**Правило:** сначала один нормальный ответ в TG, потом модели/голос/поиск.

## Шаги

### 1. Клон и bootstrap

```bash
git clone https://github.com/ManSio/gemma_agent.git /opt/gemma_agent
cd /opt/gemma_agent
bash scripts/agent_bootstrap.sh
```

### 2. Минимум в `.env`

```env
TELEGRAM_TOKEN=
OPENROUTER_API_KEY=
ADMIN_USER_IDS=
OWNER_TELEGRAM_ID=
USER_ACCESS_APPROVAL_REQUIRED=true
SEARXNG_ENABLED=true
SEARXNG_INSTANCE_URL=http://127.0.0.1:8080
MEM0_LOCAL=true
MEM0_API_URL=http://127.0.0.1:8001
```

### 3. Старт

```bash
bash scripts/gemma_panel.sh start-all
```

### 4. Проверка в TG

1. `/start`
2. `погода в Минске`
3. `какие новости` (нужен SearXNG)

### 5. Безопасность

```bash
python scripts/agent_security_audit.py --quick
```

## Windows

```powershell
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.\venv\Scripts\python main.py
```

Остановите бот на VPS, если тот же токен.

## Дальше

- [Установка](installation.ru.md)
- [Конфигурация](configuration.ru.md)
- [Проблемы](../user-guide/troubleshooting.ru.md)
