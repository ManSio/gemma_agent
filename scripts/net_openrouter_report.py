#!/usr/bin/env python3
"""
Снимок сети + каталог OpenRouter + опциональный мини-бенч модели.

  python scripts/net_openrouter_report.py --probes
  python scripts/net_openrouter_report.py --models --models-limit 80
  python scripts/net_openrouter_report.py --bench --model google/gemini-2.0-flash-001

Загружает .env из корня проекта. Кэш каталога: OPENROUTER_MODELS_CACHE_SEC (сброс: --models-fresh).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _amain(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    out: dict = {}

    if args.probes:
        from core.network_probe import run_http_latency_probes

        out["http_probes"] = await run_http_latency_probes()

    if args.connectivity:
        from core.connectivity_check import run_connectivity_checks

        out["connectivity"] = await run_connectivity_checks(include_http_probes=bool(args.probes_inline))

    if args.models:
        from core.openrouter_catalog import get_openrouter_models_catalog, sort_models_for_display

        rows = await get_openrouter_models_catalog(force_refresh=args.models_fresh)
        sorted_rows = sort_models_for_display(rows, prefer_free=not args.models_alpha)
        lim = max(1, min(args.models_limit, len(sorted_rows)))
        out["models_total"] = len(rows)
        out["models_sample"] = sorted_rows[:lim]
        out["models_note"] = (
            "Цены: USD за 1M токенов (из полей pricing в API). "
            "likely_free_route — эвристика (prompt и completion ≈ 0). "
            "Скорость генерации смотри --bench; длинный ответ сам по себе дольше."
        )

    if args.bench:
        import os

        from core.openrouter_catalog import openrouter_completion_benchmark

        model = (args.model or os.getenv("OPENROUTER_MODEL_FREE") or "").strip()
        key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
        if not model or not key:
            out["bench_error"] = "Нужны OPENROUTER_API_KEY и --model или OPENROUTER_MODEL_FREE"
        else:
            out["bench"] = await openrouter_completion_benchmark(
                api_key=key,
                model=model,
                max_tokens=args.bench_max_tokens,
            )

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Сеть и OpenRouter: зонды, каталог, бенч")
    p.add_argument("--probes", action="store_true", help="Параллельные HTTP GET (OpenRouter, Telegram host, Cloudflare)")
    p.add_argument("--connectivity", action="store_true", help="Полная проверка как в check_connectivity (нужны ключи в .env)")
    p.add_argument("--probes-inline", action="store_true", help="С connectivity добавить те же HTTP-зонды в один отчёт")
    p.add_argument("--models", action="store_true", help="Скачать каталог моделей (кэш OPENROUTER_MODELS_CACHE_SEC)")
    p.add_argument("--models-fresh", action="store_true", help="Игнорировать кэш каталога")
    p.add_argument("--models-limit", type=int, default=60, help="Сколько моделей показать в models_sample")
    p.add_argument("--models-alpha", action="store_true", help="Сортировать по id, а не «сначала бесплатные»")
    p.add_argument("--bench", action="store_true", help="Короткий completion + токены/сек")
    p.add_argument("--model", type=str, default="", help="Модель для --bench")
    p.add_argument("--bench-max-tokens", type=int, default=96, help="Потолок max_tokens для бенча")
    args = p.parse_args()
    if not (args.probes or args.connectivity or args.models or args.bench):
        p.print_help()
        return 1
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
