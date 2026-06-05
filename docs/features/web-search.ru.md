# Веб-поиск (SearXNG)

UniversalSearch ходит в ваш SearXNG — запросы не через случайный публичный API.

## `.env`

```env
SEARXNG_ENABLED=true
SEARXNG_INSTANCE_URL=http://127.0.0.1:8080
SEARXNG_MAX_RESULTS=8
```

## Установка

```bash
sudo bash scripts/searxng_install_native.sh
```

Шаблон: `infra/searxng/settings.yml`

## Другой хост в LAN

```env
SEARXNG_INSTANCE_URL=http://10.0.0.10:8080
```

Бот должен достучаться до URL.

## Проверка в TG

«какие новости» — статья со ссылками на источники.

Без SearXNG — ответы хуже привязаны к фактам.
