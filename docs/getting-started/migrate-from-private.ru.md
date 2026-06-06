# Замена приватного бота на public build (один VPS)

Аккуратная замена **на месте**: `.env` + `data/`, два уровня отката, без второй копии в RAM.

## Скрипты

| Скрипт | Назначение |
|--------|------------|
| `scripts/migrate_private_to_public.sh` | Замена → public |
| `scripts/rollback_to_private.sh` | Откат → приватная папка или tarball |

## Перед миграцией

1. Выбери окно **5–10 мин** простоя Telegram-бота  
2. Убедись в месте на диске: **≈ 2× размер каталога бота**  
3. Запиши путь бота (часто `/srv/gemma_bot` или `/opt/gemma_agent`)

```bash
du -sh /srv/gemma_bot
df -h /
```

## Шаги на VPS

```bash
# 1. Остановка
sudo systemctl stop gemma_bot.service
cd /srv/gemma_bot && bash scripts/gemma_panel.sh stop-all 2>/dev/null || true

# 2. Обновить скрипт (после git pull с GitHub) или скопировать вручную
git pull origin master

# 3. План без изменений
bash scripts/migrate_private_to_public.sh --bot-dir /srv/gemma_bot --dry-run

# 4. Миграция (~3–8 мин)
bash scripts/migrate_private_to_public.sh --bot-dir /srv/gemma_bot

# 5. Старт
bash scripts/gemma_panel.sh start-all
python scripts/gemma_status.py --online
```

Проверь в Telegram: `/start`, ответ на сообщение, память (если была).

## Что делает скрипт

1. Копия `.env` отдельно в `/var/backups/gemma/`  
2. Полный `tar.gz` всего каталога  
3. Переименование старого → `*_private_YYYYMMDD*`  
4. `git clone` public `master`  
5. Перенос `.env` + `data/` (**не** приватный `modules_catalog`)  
6. `sync_env_from_example.py` — новые ключи, секреты сохраняются  
7. Новый `venv` через `agent_bootstrap.sh`  
8. `release_guard.py --smoke`  

При ошибке до финала — **авто-откат** переименованной папки на место.

## Откат

Файл с инструкцией: `/var/backups/gemma/ROLLBACK_*.txt`

**Быстро (переименованная папка):**

```bash
sudo systemctl stop gemma_bot.service
bash scripts/rollback_to_private.sh \
  --rollback-dir /srv/gemma_bot_private_20260606T120000Z \
  --bot-dir /srv/gemma_bot
sudo systemctl start gemma_bot.service
```

**Из архива:**

```bash
bash scripts/rollback_to_private.sh \
  --archive /var/backups/gemma/gemma_private_full_20260606T120000Z.tar.gz \
  --bot-dir /srv/gemma_bot
```

## После миграции

- Снятые модули (spatial, LawSearch runtime, …) **не вернутся** без отката на приватную папку  
- Mem0 / behavior в `data/` должны сохраниться  
- При странном поведении — откат, не «чинить на живую»

**EN:** [migrate-from-private.md](migrate-from-private.md)
