# Модель безопасности (честно)

Что Gemma Agent реально защищает — и что нет.

## Что есть в коде

| Слой | Назначение |
|------|------------|
| `check_public_privacy.py` | Скан git-файлов на IP, токены, реальные user id |
| `SecurityManager` | Флуд, подозрительные ссылки, предупреждения по файлам |
| Модуль `security_layer` | Опциональное Fernet-шифрование для tool payload (`ENCRYPTION_KEY`) |
| `USER_ACCESS_APPROVAL_REQUIRED` | Новые пользователи ждут одобрения админа |
| `ADMIN_USER_IDS` | Ограничение `/admin_*`, `/diag` |
| Anti-flood | Лимиты сообщений на пользователя |
| `.gitignore` | `.env`, `data/`, локальные blocklist |

## Чего нет / ограничения

1. **Граница LLM** — Текст пользователя и веб-контент уходят в OpenRouter. Prompt injection возможен. Нет криптографической «правды» ответа.

2. **Mem0 stub** — Память в plain JSON на диске. Поиск по подстроке, без изоляции арендаторов.

3. **Нет E2E** — Цепочка Telegram ↔ бот ↔ LLM не зашифрована end-to-end сверх транспорта Telegram.

4. **SearXNG** — Запросы видны вашему инстансу и движкам поиска.

5. **Голос в облако** — При `VOICE_STT_FALLBACK_BACKEND=openrouter` аудио может уйти наружу.

6. **Ошибка конфига** — `USER_ACCESS_APPROVAL_REQUIRED=false` открывает бота всем по ссылке.

7. **CVE в зависимостях** — Периодически `pip audit`; `release_guard` не заменяет сканер уязвимостей.

## Команды аудита

```bash
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
- [ ] Вместо stub — Mem0 с auth для недоверенных пользователей  
- [ ] `TELEGRAM_REPLY_MODE_FOOTER=off` для обычных пользователей  

## Сообщить об уязвимости

Владельцу репозитория в личку. Без токенов и дампов пользователей в публичных issues.
