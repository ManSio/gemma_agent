Аудит **ManSio/gemma_agent**. Skill: `gemma-deep-audit` + `gemma-agent`.

## Тема
[архитектура / security / тесты / модуль X]

## Обязательно
```bash
python scripts/print_repo_stats.py
python -m pytest tests/ --collect-only -q | tail -1
wc -l core/orchestrator.py
grep -cE '^[A-Z_][A-Z0-9_]*=' .env.example
```

Прочитать: `SECURITY.md`, `docs/HONEST_POSITIONING.md`, `docs/PRODUCTION_EVIDENCE_REPORT.md` §0 §10.

## Правила
- Вердикт **после** файлов и команд
- Не читал файл → напиши «не читал», не оценивай
- Публичный GitHub **2026-06-06**, прод с **2026-05-02** — не «1 день»
- Таблицы 9/10 в доках = **самооценка**, не консенсус
- Формат: `.cursor/skills/gemma-deep-audit/reference.md`
