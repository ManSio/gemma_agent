Работа в **gemma_agent**. Следуй skill `gemma-agent` и rules `.cursor/rules/`.

## Задача
[опиши баг или фичу]

## Требования
1. Сначала **поток**: input → decision → action → output
2. Прочитай callers и тесты **до** правки
3. **Минимальный diff** — без drive-by в `orchestrator.py`
4. Новые env → `.env.example` с комментарием
5. Security: `access_gate`, `safe_paths`, без секретов в логах
6. **Verify** до «готово»: targeted pytest или smoke
7. В отчёте: что проверено, что не запускал

## Не делать
- Коммит без моей просьбы
- Угадывать содержимое файлов
- Рефактор «заодно»
