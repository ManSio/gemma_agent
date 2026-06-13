#!/usr/bin/env python3
"""Проба: дайджест → выбор пункта (без Telegram). Запуск из корня репо."""
from __future__ import annotations

import asyncio
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_ROOT, ".env"), override=False)


async def main() -> int:
    from core.news_reply import try_news_item_reply, try_news_reply

    persisted: dict = {"user_facts": {"country_code": "BY"}}
    recent: list = []

    digest = await try_news_reply(
        "какие новости в мире",
        persisted=persisted,
        user_id="probe",
        recent_dialogue=recent,
    )
    if not digest:
        print("FAIL: no digest")
        return 1
    print("=== DIGEST (first 1200 chars) ===")
    print(digest[:1200])
    print()

    recent = [
        {"role": "user", "text": "какие новости в мире"},
        {"role": "assistant", "text": digest},
    ]
    ds = persisted.get("dialogue_state") or {}
    items = ds.get("last_news_digest_items") or []
    print(f"stash items: {len(items)}")
    if items:
        for row in items[:3]:
            print(
                f"  #{row.get('index')} snip={len(str(row.get('snippet') or ''))} "
                f"has_google_link={bool(row.get('google_link'))}"
            )

    pick = 3 if len(items) >= 3 else 1
    detail = await try_news_item_reply(
        str(pick),
        persisted=persisted,
        user_id="probe",
        recent_dialogue=recent,
    )
    if not detail:
        print(f"FAIL: no detail for pick {pick}")
        return 1
    print(f"=== ITEM {pick} ===")
    print(detail[:2000])
    low = detail.lower()
    bad = ("что будем искать", "прайс-лист", "медиакит")
    if any(b in low for b in bad):
        print("WARN: homepage chrome in reply")
        return 2
    if "краткого текста" in low or "не удалось подтянуть" in low:
        print("WARN: empty stub")
        return 2
    if detail.count(";") >= 2 and detail.count(" - ") >= 3:
        print("WARN: multi-headline blob in item reply")
        return 2
    if "по заголовку из новостной ленты" in low and len(detail) < 220:
        print("WARN: only headline fallback (search/fetch down)")
        return 2
    if len(detail) < 180:
        print("WARN: very short reply")
        return 2
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
