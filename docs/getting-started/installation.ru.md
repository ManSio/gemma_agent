# Установка

## Требования

| Компонент | Версия |
|-----------|--------|
| Python | 3.11+ |
| ОС | Linux (рекомендуется), macOS, Git Bash |
| CPU | 1 ядро (2 для dev) |
| RAM | **1 GB + swap** (впритык; VPN на том же хосте опционален), **4 GB рекомендуется** |
| Диск | 5 GB мин., **10+ GB** на VPS с логами |
| GPU | **Не нужен** — LLM через OpenRouter |
| Сеть | HTTPS до OpenRouter; LAN для SearXNG/Mem0 при необходимости |

**Проверено на реальном железе:** VPS 1 GB (legacy, опционально VPN), VPS 4 GB (прод), LAN lab 3.5 GB — [Системные требования](../SYSTEM_REQUIREMENTS.md).

## Автоустановка

```bash
cd /opt/gemma_agent
bash scripts/agent_bootstrap.sh
```

Создаёт: venv, `.env`, `gemma_panel.local.conf` (Mem0 stub), каталоги `data/`.

## Панель

```bash
bash scripts/gemma_panel.sh start-all
bash scripts/gemma_panel.sh status
```

См. [Панель](../user-guide/panel.ru.md).

## Внешние сервисы

| Сервис | Нужен | Установка |
|--------|:-----:|-----------|
| OpenRouter | да | Ключ в `.env` |
| SearXNG | очень желательно | `sudo bash scripts/searxng_install_native.sh` |
| Mem0 | желательно | Stub по умолчанию или `apply_mem0_local_server.sh` |
| Piper TTS | опционально | `models/piper/` |

- [Поиск](../features/web-search.ru.md)
- [Память](../features/memory.ru.md)
- [Голос](../features/voice.ru.md)

## Проверка

```bash
python scripts/gemma_status.py --online
bash scripts/gemma_panel.sh mem0-health
```

## Права на data/

```bash
GEMMA_FIX_DATA_OWNER=1 bash scripts/gemma_host_setup.sh /opt/gemma_agent
```
