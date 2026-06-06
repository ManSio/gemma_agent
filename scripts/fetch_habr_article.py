#!/usr/bin/env python3
"""
Пример «как в туториале»: HTTP + разбор HTML (здесь — через SiteRecipe / пресет Хабра).

Статья-референс: https://habr.com/ru/articles/1025760/
Запуск из корня проекта:
  python scripts/fetch_habr_article.py
  python scripts/fetch_habr_article.py https://habr.com/ru/articles/1025760/
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def _run(url: str) -> int:
    os.environ.setdefault("SITE_RECIPE_ENABLED", "true")
    # Подхватываем .env при наличии
    try:
        from dotenv import load_dotenv

        load_dotenv(_ROOT / ".env")
    except ImportError:
        pass

    from core.site_recipe_module import SiteRecipeModule

    mod = SiteRecipeModule()
    r = await mod.parse_with_recipe(url, user_id="cli")
    if r.get("error"):
        print(r["error"], file=sys.stderr)
        return 1
    title = (r.get("title") or "").strip()
    if title:
        print(title)
        print("-" * 60)
    print((r.get("text") or "").strip())
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Вывести текст статьи Хабра (безопасный fetch + пресет/рецепт).")
    p.add_argument(
        "url",
        nargs="?",
        default="https://habr.com/ru/articles/1025760/",
        help="URL статьи (по умолчанию статья из запроса)",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="Не использовать SITE_RECIPE_CACHE (установит SITE_RECIPE_CACHE_SKIP=1)",
    )
    args = p.parse_args()
    if args.no_cache:
        os.environ["SITE_RECIPE_CACHE_SKIP"] = "1"
    raise SystemExit(asyncio.run(_run(args.url.strip())))


if __name__ == "__main__":
    main()
