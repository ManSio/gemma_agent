# Модель безопасности (честно)

Что Gemma Agent реально защищает — и что нет.

## Что есть в коде

| Слой | Назначение |
|------|------------|
| `check_public_privacy.py` | Скан git-файлов на IP, токены, реальные user id |
| `SecurityManager` | Флуд, подозрительные ссылки, предупреждения по файлам |
| Модуль `security_layer` | Шифрование tool payload (`ENCRYPTION_KEY`) |
| `encrypted_json_store` | Mem0 stub + `facts.json` на диске с Fernet при `ENCRYPTION_KEY` |
| `prompt_injection_guard` | Фильтр injection-строк в тексте пользователя |
| `untrusted_content_sanitize` | Санитизация веб/вставок |
| `pipeline_early_guards` | Блок exfiltration без вызова LLM |
| `USER_ACCESS_APPROVAL_REQUIRED` | Новые пользователи ждут одобрения админа |
| `ADMIN_USER_IDS` | Ограничение `/admin_*`, `/diag` |
| Anti-flood | Лимиты сообщений на пользователя |
| `.gitignore` | `.env`, `data/`, локальные blocklist |

## Чего нет / ограничения

1. **Граница LLM** — Смягчено: фильтр строк, early guards, sanitize веба. Сложный injection всё ещё возможен.

2. **Mem0 stub** — С `ENCRYPTION_KEY` файл на диске зашифрован (`chmod 600`). Без ключа — plain JSON (только dev).

3. **Нет E2E на уровне приложения** — Транспорт Telegram шифрует Telegram. Память на диске — через `ENCRYPTION_KEY`.

4. **SearXNG** — Запросы видны вашему инстансу и движкам поиска.

5. **Голос в облако** — При `VOICE_STT_FALLBACK_BACKEND=openrouter` аудио может уйти наружу.

6. **Ошибка конфига** — `USER_ACCESS_APPROVAL_REQUIRED=false` открывает бота всем по ссылке.

7. **CVE в зависимостях** — Периодически `pip audit`; `release_guard` не заменяет сканер уязвимостей.

## Команды аудита

```bash
python scripts/generate_encryption_key.py
python scripts/agent_security_audit.py
python scripts/check_public_privacy.py --ci
python scripts/release_guard.py
pytest tests/test_security_layer.py -q
```

Код выхода 1 — не называйте релиз «безопасным», пока не исправите.

## Чеклист продакшена

- [ ] `.env` chmod 600, не в git  
- [ ] `ADMIN_USER_IDS` / `OWNER_TELEGRAM_ID` только реальные админы  
- [ ] `USER_ACCESS_APPROVAL_REQUIRED=true` (если не публичная beta)  
- [ ] Ротация токенов при утечке в чат/лог/git  
- [ ] SearXNG на localhost/LAN  
- [ ] `ENCRYPTION_KEY` в `.env` (шифрование памяти на диске)  
- [ ] Вместо stub — Mem0 с auth для недоверенных пользователей  
- [ ] `TELEGRAM_REPLY_MODE_FOOTER=off` для обычных пользователей  

## Сообщить об уязвимости

Владельцу репозитория в личку. Без токенов и дампов пользователей в публичных issues.
