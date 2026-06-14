# Code scanning и утечки данных

Три **разных** слоя — не путать.

| Слой | Инструмент | Что ловит | Блокирует CI? |
|------|------------|-----------|---------------|
| **PII в git** | `scripts/check_public_privacy.py --ci` | Telegram ID, токены в tracked файлах | **Да** (`ci.yml` privacy job) |
| **Security audit** | `scripts/agent_security_audit.py --ci` | .env, токены, smoke | **Да** |
| **CodeQL** | [GitHub Code scanning](https://github.com/ManSio/gemma_agent/security/code-scanning) | Clear-text в лог/файл, ReDoS | **Нет** (workflow зелёный = анализ завершён) |

## Перед коммитом (обязательно для агентов и людей)

```bash
bash scripts/pre_commit_checks.sh
# или вручную:
python scripts/check_public_privacy.py --ci
PYTHONPATH=. python scripts/agent_security_audit.py --ci
```

Правило Cursor: `.cursor/rules/pre-commit-privacy.mdc`

## CodeQL: clear-text storage / logging

**Исправлено в коде (v3.5.28+):**

- `core/ops_trace.py` — на диск только `sanitize_ops_trace_row_for_disk()` (хеши + длины).
- `core/llm_usage_store.py` — whitelist через `llm_usage_row_for_disk()`.
- `core/ephemeral_autolearn.py` — логи без raw `user_id`.

Полные тексты ходов для forensic — `data/runtime/turns.jsonl` на VPS (не в git).

## CodeQL: ReDoS (warning)

Предупреждения `py/polynomial-redos` в `telegram_output_guard.py` и др. — regex на user text.

Митигация:

- `core/regex_safe.py` — `cap_regex_input()` / `REGEX_INPUT_MAX_LEN`
- Постепенный перевод hot paths на `regex_safe`

До полного rollout — warnings допустимы; следить в Security tab.

## Тестовые ID

Только из `tests/fixtures/telegram_test_ids.py` (`900000001` …).  
Prod uid — `GEMMA_PROBE_USER_ID` на VPS, не в репозитории.
