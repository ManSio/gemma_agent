# Каталог переменных `.env`

## Полная документация (все ~860 переменных)

| Источник | Назначение |
|----------|------------|
| **`.env.example`** | Главный справочник: у каждой переменной комментарий, true/false, пример |
| `config/env_catalog/generated/*.env.fragment` | Те же блоки по секциям (для `apply_env_catalog`) |
| `config/env_catalog/05_*.fragment` … `30_*.fragment` | Ручные переопределения (новости, PERSONAL_PROD) |

Пересобрать комментарии и фрагменты из example:

```bash
python scripts/enrich_env_example.py --write --build-catalog
```

## Синхронизация рабочего `.env` (локально / сервер)

Сохраняет **ваши секреты и значения**, подставляет **структуру и комментарии** из `.env.example`:

```bash
python scripts/sync_env_from_example.py
python scripts/sync_env_from_example.py /opt/gemma_agent/.env
```

Id из `docs/OPS_PRIVATE.local.md` (не в git) подставляются для `OWNER_TELEGRAM_ID`, `POST_DEPLOY_PROBE_USER_ID`.

## Только флаги без переписывания файла

```bash
python scripts/apply_env_catalog.py
python scripts/apply_env_catalog.py /opt/gemma_agent/.env
```

Обновляет/добавляет ключи из каталога; секреты и непустые `ADMIN_USER_IDS` не затирает.

## На сервере после `git pull`

```bash
cd /opt/gemma_agent
python scripts/sync_env_from_example.py /opt/gemma_agent/.env
python scripts/apply_env_catalog.py /opt/gemma_agent/.env
bash scripts/gemma_panel.sh restart
```

Скопируйте `docs/OPS_PRIVATE.local.md` на сервер, если нужны автоматические id в apply.
