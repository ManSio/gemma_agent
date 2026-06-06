# Mem0 на сервере (вне git gemma_bot)

Каталог **`/opt/mem0_local`** на `HOST_LAN` — **отдельная** установка (`mem0_server.py`, `memory.db`, свой venv).  
В репозиторий **gemma_bot_v2** этот код **не входит** (там секреты и бинарная БД).

## Варианты

| Вариант | Git pull | Память |
|---------|----------|--------|
| **A. Как сейчас на deploy-host** | только gemma_bot | `/opt/mem0_local` + панель `mem0-start` |
| **B. Заглушка из репо** | gemma_bot | `GEMMA_MEM0_USE_STUB=true` в `gemma_panel.local.conf` |

Вариант B: см. [docs/MEM0_LOCAL_FOR_GEMMA_RU.md](../../docs/MEM0_LOCAL_FOR_GEMMA_RU.md).

## Бэкап перед «чистой» переустановкой

```bash
cp -a /opt/mem0_local/memory.db ~/memory.db.backup
```

## Проверка

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/docs
```
