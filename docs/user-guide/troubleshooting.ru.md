# Решение проблем

| Симптом | Что делать |
|---------|------------|
| Бот молчит | `status`; второй процесс с тем же токеном |
| Нет поиска | `curl` на SearXNG URL |
| Mem0 | `mem0-health`, `mem0-start` |
| Напоминания не в том поясе | `/me` |
| Двойной привет | Косметика |
| Футер «погода» не по теме | Залипший слот в behavior — новый диалог |
| Права data/ | `gemma_host_setup.sh` с `GEMMA_FIX_DATA_OWNER=1` |

```bash
python scripts/gemma_status.py --online
bash scripts/gemma_panel.sh preflight
```

Issues: [REPO_LINKS.md](../REPO_LINKS.md)
