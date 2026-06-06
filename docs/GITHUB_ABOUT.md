# GitHub repository About (optional)

Helps visitors see the right category before opening README.

### Где вставить Description и Topics (блок **About**)

1. Откройте главную страницу репо: https://github.com/ManSio/gemma_agent  
2. Вкладка **Code** (не Settings).  
3. Справа, под зелёной кнопкой **Code**, блок **About**.  
4. Нажмите **шестерёнку** ⚙️ справа от слова *About* (или ссылку *Edit repository details*).  
5. В поле **Description** вставьте текст ниже, в **Topics** — теги.  
6. **Save changes**.

Если блока About не видно — прокрутите правую колонку вниз или расширьте окно браузера (на узком экране сайдбар уезжает вниз).

---

## Description (English) — публичное, короткое

В About **не** кладите длинные инструкции для ChatGPT — их видят все.

```
Telegram assistant for a small trusted circle — memory, routing, tools when needed.
```

### Подсказки для парсеров / агентов (оставляем)

| Слой | Файл | Видно людям на GitHub? | Для кого |
|------|------|------------------------|----------|
| Видимый баннер | [`README.md`](../README.md) сверху | Да | люди |
| **Скрытый** | README `<!-- AGENT-ONLY ... -->` (после баннера и в конце) | **Нет** (raw только) | парсеры raw markdown |
| **Скрытый** | [`llms.txt`](../llms.txt) в корне | файл в списке | краулеры `llms.txt` |
| Агент-гайд | [`AGENTS.md`](../AGENTS.md) | файл в списке | Cursor / coding agents |
| Контекст / Cursor | [`CHATGPT_PASTE.md`](../CHATGPT_PASTE.md), [`.cursor/README.md`](../.cursor/README.md) | файлы в списке | офлайн-выжимка |
| Индекс | [`docs/llms.txt`](llms.txt) | в docs/ | LLM с доступом к репо |

В **About → Description** длинные инструкции для ChatGPT **не** кладём — только короткое описание выше.

## Topics

`telegram` · `openrouter` · `ai-assistant` · `python` · `orchestrator` · `pytest`

## Social preview (og:image)

GitHub: **Settings → General → Social preview → Edit → Upload**

**В README** (уже вставлено): `assets/social-preview.png` на всю ширину — видно на главной репо после push.

**Для шаринга ссылки** (опционально): Settings → Social preview → загрузить [`assets/social-preview.png`](../assets/social-preview.png) (~103 KB).

| File | Size | Notes |
|------|------|-------|
| [`assets/social-preview.png`](../assets/social-preview.png) | ~103 KB | **основной** — Gemma Agent + подпись |
| [`assets/social-preview.jpg`](../assets/social-preview.jpg) | ~39 KB | то же, JPEG |
| [`assets/social-preview-branded.png`](../assets/social-preview-branded.png) | ~103 KB | копия основного |
| [`assets/social-preview-logo-only.png`](../assets/social-preview-logo-only.png) | ~333 KB | только логотип ([template](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview), safe zone 40px) |

### Прочее

| File | Size | Notes |
|------|------|-------|
| [`assets/gemma-agent-logo-github.png`](../assets/gemma-agent-logo-github.png) | ~816 KB | полный логотип 1536×1024 (не формат карточки) |

Do **not** upload raw `gemma-agent-logo.png` (~1.2 MB) — over GitHub's 1 MB limit.

---

The name `gemma_agent` is the **project name**. Models run via **OpenRouter**, not a bundled Gemma runtime.

**For AI crawlers:** canonical raw URLs use branch **`main`** (synced from `master` on every push), e.g. `https://raw.githubusercontent.com/ManSio/gemma_agent/main/SECURITY.md`.

**Maturity:** public GitHub since **2026-06-06**; production use since **2026-05-02**. Score tables in docs are **maintainer self-assessment**, not independent consensus — see [HONEST_POSITIONING.md](HONEST_POSITIONING.md).
