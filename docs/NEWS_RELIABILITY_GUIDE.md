# NEWS_RELIABILITY_GUIDE — Руководство по надёжности новостного парсинга

## Что изменилось (до/после)

### До рефакторинга
- Логика парсинга была размазана по модулям
- Отсутствовала стандартная структура статьи
- Генерация дисклеймера была встроена в reply-логику

### После рефакторинга
- **core/news_article_model.py** — TypedDict модель
- **core/news_validator.py** — валидация fetch
- **core/news_disclaimer.py** — генератор дисклеймера
- **core/news_consistency_checker.py** — проверка противоречий

## Архитектура
```
User Query -> [URL Fetcher] -> [NewsValidator] -> [NewsArticle] -> [DisclaimerGenerator] -> [ConsistencyChecker] -> LLM Reply
```

## Уровни Confidence
| Уровень | Значение | Условие |
|---|---|---|
| HIGH | 0.70–1.00 | Все доверенные + conf >= 0.7 |
| MEDIUM | 0.30–0.69 | avg conf >= 0.3 |
| LOW | 0.00–0.29 | conf < 0.3 или нет источников |

## Мониторинг
- grep -i "cloudflare" /var/log/gemma/news-*.log
- grep -i "captcha" /var/log/gemma/news-*.log
- grep "HTTP 404" /var/log/gemma/news-*.log
- grep "Text too short" /var/log/gemma/news-*.log
- jq .valid /var/log/gemma/news.jsonl | sort | uniq -c
- jq .confidence /var/log/gemma/news.jsonl | awk '{s+=$1; n++} END {print s/n}'

## Troubleshooting
1. **404/403** — URL устарел -> fallback web_search
2. **Cloudflare/Captcha** — WAF -> alternative_method="web_search"
3. **Короткий текст** — ленивая загрузка -> fallback_fetch()
4. **Conflict в диалоге** — разные даты -> warn_user
