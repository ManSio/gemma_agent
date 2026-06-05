# Админ и эксплуатация

Нужен `ADMIN_USER_IDS` в `.env`.

## В Telegram

| Команда | Назначение |
|---------|------------|
| `/diag` | Краткая сводка |
| `/admin_xray` | Рентген пайплайна |
| `/admin_diagnostic` | ZIP диагностики |

## На сервере

```bash
python scripts/gemma_status.py --online
python scripts/turns_search.py "погода" --days 3
```

## Логи

- `data/users/logs/gemma_bot.log`
- `data/runtime/turns.jsonl` — метаданные ходов
- Полный диалог: `data/users/behavior/*.json`

## Автопилот (опционально)

`GEMMA_AUTOPILOT_MODE=on` — дайджесты в личку админу.

## Выключено на проде намеренно

MCE, goal runner auto, mesh, dormant-модули.
