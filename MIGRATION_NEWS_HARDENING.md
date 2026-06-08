# Migration Guide: v3.4.x → v3.5.0 (News Reliability Hardening)

## Overview

v3.5.0 adds mandatory source attribution, fetch validation, self-verify with source context, auto-disclaimers, and turn-level consistency checking for all news replies.

## Breaking Changes

### 1. `format_news_from_search()` — new `sources` parameter

```python
# OLD (v3.4):
reply = format_news_from_search(summary, user_query=user_query)

# NEW (v3.5):
reply = format_news_from_search(
    summary,
    user_query=user_query,
    sources=source_list,  # ← REQUIRED for disclaimer
)
```

Where `source_list` is `List[Dict[str, Any]]` with keys:
- `url`, `domain`, `fetch_method`, `fetch_success`, `text_length`, `parsing_confidence`

### 2. `format_news_loose_from_summary()` — new `sources` parameter

```python
# OLD (v3.4):
reply = format_news_loose_from_summary(summary, user_query=user_query)

# NEW (v3.5):
reply = format_news_loose_from_summary(
    summary,
    user_query=user_query,
    sources=source_list,  # ← REQUIRED for disclaimer
)
```

### 3. `run_self_verify()` — new `source_context` parameter

```python
# OLD (v3.4):
ver = await run_self_verify(reply, user_text, llm, clock_info=..., user_name=...)

# NEW (v3.5):
ver = await run_self_verify(
    reply, user_text, llm,
    clock_info=...,
    user_name=...,
    source_context="...",  # ← OPTIONAL, enables hallucination check
)
```

### 4. `_fetch_page_article()` now returns `Dict` compatible with `NewsArticle`

The return dict now includes `"title"` and `"confidence"` keys in addition to existing `"text"`, `"images"`, `"url"`.

## New Dependencies

None. All new modules use standard library only.

## New Environment Variables

None required. All features auto-enabled when `SELF_VERIFY_ACTIVE=true`.

## Verification

After migration, run:

```bash
python -m pytest tests/test_news_*.py -v
# Expected: 37 passed, 0 failed