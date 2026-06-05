# Тестирование

```bash
python -m pytest tests/ -q
python scripts/release_guard.py
python scripts/check_public_privacy.py --ci
python scripts/agent_security_audit.py
```

## Уровни

| Уровень | Команда |
|---------|---------|
| Smoke | `release_guard.py --smoke` |
| Anti-regression | `release_guard.py` |
| Полный | `release_guard.py --full` |

Зелёный pytest ≠ проверка в TG — после смены поведения smoke в Telegram.

См. `tests/README.md`
