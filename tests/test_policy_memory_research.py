"""EASMO-style offline check: policy memory vs generic trim on ACC routes."""
from __future__ import annotations

import unittest

from core.research.policy_memory import (
    SCENARIOS,
    jaccard_tokens,
    run_agentic_matrix,
    run_matrix,
    run_saturation_report,
    tokenize_ru,
)


class PolicyMemoryResearchTests(unittest.TestCase):
    def test_wrong_transfer_news_memory_fails_facts_route(self) -> None:
        report = run_matrix()
        wt = report["wrong_transfer"]
        self.assertLess(wt["news_on_facts_recall"], 0.5)
        self.assertGreaterEqual(wt["slots_on_facts_recall"], 0.75)

    def test_article_and_news_memories_are_distinct(self) -> None:
        report = run_matrix()
        self.assertLess(report["overlap"]["article_vs_news_jaccard"], 0.25)

    def test_slots_beat_generic_trim_on_article_followup(self) -> None:
        report = run_matrix()
        rows = report["rows"]
        article = [
            r
            for r in rows
            if r["scenario"] == "acc_article_followup" and r["route"] == "article_followup"
        ]
        trim1 = next(r for r in article if r["policy"] == "generic_last_1")
        slots = next(r for r in article if r["policy"] == "slots_plus_last_10")
        route_best = next(r for r in article if r["policy"] == "route_article_followup")
        self.assertGreater(slots["recall"], trim1["recall"])
        self.assertGreaterEqual(route_best["recall"], slots["recall"])

    def test_mismatched_route_memory_fails(self) -> None:
        report = run_matrix()
        rows = report["rows"]
        article = [r for r in rows if r["scenario"] == "acc_article_followup" and r["route"] == "article_followup"]
        facts = [r for r in rows if r["scenario"] == "acc_facts_da" and r["route"] == "facts_confirm"]
        news_on_article = next(r for r in article if r["policy"] == "route_news")
        article_on_facts = next(r for r in facts if r["policy"] == "route_article_followup")
        self.assertEqual(news_on_article["recall"], 0.0)
        self.assertEqual(article_on_facts["recall"], 0.0)

    def test_crimea_followup_slots_recall(self) -> None:
        report = run_matrix()
        rows = [
            r
            for r in report["rows"]
            if r["scenario"] == "acc_crimea_followup" and r["route"] == "article_followup"
        ]
        slots = next(r for r in rows if r["policy"] == "slots_plus_last_10")
        self.assertGreaterEqual(slots["recall"], 0.66)

    def test_pivot_weather_route_beats_news_route(self) -> None:
        report = run_matrix()
        rows = [r for r in report["rows"] if r["scenario"] == "acc_pivot_weather" and r["route"] == "pivot_weather"]
        pivot = next(r for r in rows if r["policy"] == "route_pivot_weather")
        news = next(r for r in rows if r["policy"] == "route_news")
        self.assertGreater(pivot["recall"], news["recall"])

    def test_recheck_route_recalls_galati(self) -> None:
        report = run_matrix()
        rows = [r for r in report["rows"] if r["scenario"] == "acc_recheck_anchor" and r["route"] == "recheck"]
        rec = next(r for r in rows if r["policy"] == "route_recheck")
        self.assertGreaterEqual(rec["recall"], 0.5)

    def test_saturation_slots_recover_trim1(self) -> None:
        sat = run_saturation_report()
        self.assertGreater(sat["slots_recover_count"], 0)
        munich = next(r for r in sat["rows"] if r["scenario"] == "acc_article_followup")
        self.assertGreater(munich["gap_slots_minus_trim1"], 0.0)

    def test_agentic_matrix_slots_win_munich_chain(self) -> None:
        ag = run_agentic_matrix()
        row = next(r for r in ag["rows"] if r["scenario"] == "agentic_munich_chain" and r["policy"] == "slots_plus_last_10")
        self.assertGreaterEqual(row["recall"], 0.66)

    def test_nine_acc_scenarios_registered(self) -> None:
        self.assertGreaterEqual(len(SCENARIOS), 9)

    def test_verdict_wrong_transfer_ok(self) -> None:
        report = run_matrix()
        self.assertTrue(report["verdict"]["wrong_transfer_ok"])

    def test_summarization_trim_risk_on_long_paste(self) -> None:
        report = run_matrix()
        rows = report["rows"]
        article = [r for r in rows if r["scenario"] == "acc_article_followup" and r["route"] == "article_followup"]
        trim1 = next(r for r in article if r["policy"] == "generic_last_1")
        slots = next(r for r in article if r["policy"] == "slots_plus_last_10")
        self.assertLess(trim1["recall"], slots["recall"])

    def test_ru_tokenizer_handles_cyrillic(self) -> None:
        toks = tokenize_ru("Аэропорт Мюнхена закрыли")
        self.assertIn("аэропорт", toks)
        self.assertIn("мюнхена", toks)

    def test_jaccard_disjoint_is_zero(self) -> None:
        self.assertEqual(jaccard_tokens(["a", "b"], ["c", "d"]), 0.0)


if __name__ == "__main__":
    unittest.main()
