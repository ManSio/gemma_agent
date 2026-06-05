# Public-сборка

Экспорт из private `gemma_bot` для открытого распространения.

## Убрано

- LawSearch, AduPadruchnik, ParentPortal, SchoolAssistant
- spatial_design и 47 dormant-модулей
- DEV_DIARY, Cursor rules, VPS/инциденты, секреты

## Осталось

- **19 модулей** (tier A + B)
- Право/учебники: UniversalSearch + DocumentCorpus + BooksRAG
- spatial_design — stubs

## Проверки

```bash
pytest tests/
python scripts/release_guard.py
python scripts/check_public_privacy.py --ci
```

Повторный экспорт — только из private репо (`export_public_agent.py`).
