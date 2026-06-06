"""Роутинг: preflight, commerce→research, pivot без reset, recent limit."""
from __future__ import annotations

import os
import unittest

from core.brain.profile_registry import (
    build_route_audit,
    context_load_recent_limit,
    get_profile,
    profile_from_text_heuristics,
    refine_profile,
)
from core.product_behavior import (
    apply_pivot_context_hygiene,
    pivot_reset_dialog_enabled,
    topic_pivot,
)


class TestRouteAuditAndContext(unittest.TestCase):
    def test_commerce_search_maps_research(self) -> None:
        self.assertEqual(
            profile_from_text_heuristics("Найди мне все про Samsung s26"),
            "research",
        )

    def test_refine_preflight_url(self) -> None:
        p = refine_profile("standard", "https://habr.com/ru/articles/123/", "general", confidence=0.99)
        self.assertEqual(p, "summarization")

    def test_standard_recent_count_default_ten(self) -> None:
        for key in ("BRAIN_STANDARD_RECENT_COUNT", "BRAIN_CONTEXT_LOAD_RECENT_LIMIT"):
            os.environ.pop(key, None)
        self.assertEqual(get_profile("standard").recent_count, 10)

    def test_standard_recent_count_env(self) -> None:
        os.environ["BRAIN_STANDARD_RECENT_COUNT"] = "11"
        try:
            self.assertEqual(get_profile("standard").recent_count, 11)
        finally:
            os.environ.pop("BRAIN_STANDARD_RECENT_COUNT", None)

    def test_context_load_limit(self) -> None:
        os.environ["BRAIN_CONTEXT_LOAD_RECENT_LIMIT"] = "12"
        try:
            self.assertEqual(context_load_recent_limit(), 12)
        finally:
            os.environ.pop("BRAIN_CONTEXT_LOAD_RECENT_LIMIT", None)

    def test_pivot_no_reset_by_default(self) -> None:
        self.assertFalse(pivot_reset_dialog_enabled())

    def test_pivot_trims_not_wipes(self) -> None:
        ctx = {
            "topic_tracking": {"current": "Найди Samsung s26"},
            "recent_messages": [{"role": "user", "text": f"m{i}"} for i in range(10)],
        }
        out = apply_pivot_context_hygiene(
            ctx,
            "почему земля круглая",
            user_id="u1",
        )
        self.assertTrue(topic_pivot("почему земля круглая", "Найди Samsung s26"))
        self.assertGreaterEqual(len(out.get("recent_messages") or []), 4)

    def test_build_route_audit(self) -> None:
        ra = build_route_audit(
            final_profile="research",
            preflight=None,
            router_profile="standard",
            router_source="llm",
            router_confidence=0.91,
            continuation_profile="",
            situation_lane="",
        )
        self.assertEqual(ra["final_profile"], "research")
        self.assertEqual(ra["router_source"], "llm")

    def test_news_brief_recent_follows_standard(self) -> None:
        os.environ["BRAIN_STANDARD_RECENT_COUNT"] = "12"
        os.environ.pop("BRAIN_NEWS_RECENT_COUNT", None)
        try:
            self.assertEqual(get_profile("news_brief").recent_count, 12)
        finally:
            os.environ.pop("BRAIN_STANDARD_RECENT_COUNT", None)

    def test_light_profile_recent_not_two(self) -> None:
        os.environ.pop("BRAIN_LIGHT_RECENT_COUNT", None)
        os.environ["BRAIN_QUICK_EXPLAIN_RECENT_COUNT"] = "8"
        try:
            self.assertEqual(get_profile("recommendation").recent_count, 8)
            self.assertEqual(get_profile("summarization").recent_count, 1)
        finally:
            os.environ.pop("BRAIN_QUICK_EXPLAIN_RECENT_COUNT", None)

    def test_pivot_epoch_bump_default_on(self) -> None:
        from core.product_behavior import pivot_epoch_bump_enabled

        self.assertTrue(pivot_epoch_bump_enabled())


if __name__ == "__main__":
    unittest.main()
