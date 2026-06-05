# Участие в разработке

Полный гайд на английском (вкладка **Contributing** на GitHub):

**[CONTRIBUTING.md](../../CONTRIBUTING.md)**

## Кратко

```bash
bash scripts/agent_bootstrap.sh
python scripts/release_guard.py
python scripts/check_public_privacy.py --ci
```

- Маленький diff, conventional commits (`fix:`, `feat:`, `docs:`)
- Без секретов в git
- Багфикс → тест на регрессию
- Документация: `docs/index.ru.md`

## Безопасность

[SECURITY.md](../../SECURITY.md) · [security-model.ru.md](../security/security-model.ru.md)

## Публикация репозитория

[PUBLISH_CHECKLIST.md](../PUBLISH_CHECKLIST.md) — описание, topics; URL — `python scripts/apply_repo_links.py`
