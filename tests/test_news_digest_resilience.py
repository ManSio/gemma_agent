import unittest
from unittest.mock import AsyncMock, patch

from core.llm_tiered import estimate_tiered_timeouts
from core.news_reply import (
    _news_digest_narrative_timeouts,
    _resolve_narrative_llm_timeouts,
)


class NewsDigestResilienceTests(unittest.TestCase):
    def test_narrative_outer_gt_base_world_brief(self):
        base, outer = _news_digest_narrative_timeouts(expanded=False, narr_style="world_brief")
        self.assertGreater(outer, base)

    def test_narrative_outer_covers_adaptive_world_brief(self):
        prompt = "Заголовки из ленты:\n\n" + ("1. Test headline\n" * 8)
        base, outer = _resolve_narrative_llm_timeouts(
            expanded=False,
            narr_style="world_brief",
            prompt=prompt,
            max_tokens=3800,
        )
        est = estimate_tiered_timeouts(
            tag="news_digest_llm_narrative",
            prompt=prompt,
            max_tokens=3800,
            base_timeout=base,
            task_tier="fast",
        )
        self.assertGreaterEqual(outer, float(est["free_timeout_sec"]) + 4.0)

    def test_narrative_outer_covers_adaptive_per_item(self):
        prompt = "Заголовки из ленты:\n\n" + ("1. Test\n" * 6)
        base, outer = _resolve_narrative_llm_timeouts(
            expanded=False,
            narr_style="per_item",
            prompt=prompt,
            max_tokens=3000,
        )
        est = estimate_tiered_timeouts(
            tag="news_digest_llm_narrative",
            prompt=prompt,
            max_tokens=3000,
            base_timeout=base,
            task_tier="fast",
        )
        self.assertGreaterEqual(outer, float(est["free_timeout_sec"]) + 4.0)

    def test_gather_search_quiet_on_failure(self):
        import asyncio

        async def run():
            with patch(
                "core.news_reply._search_pack",
                AsyncMock(side_effect=TimeoutError("slow")),
            ):
                from core.news_reply import _gather_digest_search_rows

                with patch("core.resilience.record_error_event") as rec:
                    rows = await _gather_digest_search_rows(
                        ["q1", "q2"],
                        country="",
                        user_id="u1",
                        world_feed=True,
                    )
                    self.assertEqual(rows, [])
                    self.assertEqual(rec.call_count, 0)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
