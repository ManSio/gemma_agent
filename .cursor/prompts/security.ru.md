Security-задача в **gemma_agent**. Skills: `gemma-agent`.

## Задача
[path traversal / access / logging / injection / export]

## Чеклист
- [ ] `core/access_gate.py` — до чувствительных handlers (voice STT, private DM)
- [ ] `core/safe_paths.py` — пользовательские пути
- [ ] `core/sensitive_export.py` — аудит/экспорт
- [ ] Логи: нет токенов, `.env`, полных сообщений
- [ ] Тест или расширение существующего security test
- [ ] `python scripts/check_public_privacy.py --ci` если трогали логи/экспорт

## Поток
Читай callers → минимальный diff → pytest → отчёт verify/not run.

Не коммить без моей просьбы.
