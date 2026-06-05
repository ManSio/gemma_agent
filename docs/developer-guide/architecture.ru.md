# Архитектура

## Суть

Сообщение TG: **вход → security → orchestrator → brain (OpenRouter) → guards → ответ**.

```
Telegram → input_layer → orchestrator → chat_orchestrator → pipeline.call_brain → Telegram
```

## Каталоги

| Путь | Роль |
|------|------|
| `main.py` | Старт, плагины |
| `core/input_layer.py` | Telegram |
| `core/orchestrator.py` | plan + execute |
| `core/brain/pipeline.py` | LLM-ход |
| `modules/` | 19 плагинов |
| `data/` | Состояние (не в git) |

## Логи

- `turns.jsonl` — метаданные ходов
- `behavior/*.json` — полный диалог

![Пайплайн](../assets/pipeline-overview.svg)
