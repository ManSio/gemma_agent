"""B3 uncertain judge hook, B5 playbook hints-only, registry merge."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.heuristic_context_gate import (
    build_topic_gate_hint,
    build_turn_decision_context,
    shortcut_allowed,
)
from core.heuristic_shortcuts_registry import load_shortcut_rules, registry_reload
from core.scenario_engine import TurnContext, forecast_pre_turn
from core.situation_playbook import apply_situation_to_forecast, match_situation, prose_blocks_playbook_lane


class HeuristicW3B5Tests(unittest.TestCase):
    def test_topic_gate_hint(self) -> None:
        h = build_topic_gate_hint({"current": "погода в Минске", "snippet": "погода"})
        self.assertIn("Тема диалога", h)
        self.assertIn("Минск", h)

    def test_negative_pattern_blocks_geo(self) -> None:
        rule = {
            "id": "geo_nearby",
            "domain": "geo",
            "block_if": [],
            "requires": ["explicit_geo_nearby"],
            "negative_patterns": ["рядом с зуб"],
        }
        ctx = build_turn_decision_context("кафе рядом с зубом на нижней челюсти")
        with patch("core.heuristic_shortcuts_registry.get_rule", return_value=rule):
            gr = shortcut_allowed("geo_nearby", ctx)
        self.assertEqual(gr.verdict, "blocked")
        self.assertEqual(gr.reason, "negative_pattern")

    def test_registry_merges_local_negative_patterns(self) -> None:
        old_root = os.environ.get("GEMMA_PROJECT_ROOT")
        try:
            with tempfile.TemporaryDirectory() as td:
                cfg = Path(td) / "config"
                cfg.mkdir()
                base = {
                    "version": 1,
                    "rules": [
                        {
                            "id": "geo_nearby",
                            "domain": "geo",
                            "block_if": [],
                            "requires": [],
                            "negative_patterns": ["alpha"],
                        }
                    ],
                }
                local = {
                    "version": 1,
                    "rules": [
                        {"id": "geo_nearby", "negative_patterns": ["beta"]},
                    ],
                }
                (cfg / "heuristic_shortcuts.json").write_text(
                    json.dumps(base, ensure_ascii=False), encoding="utf-8"
                )
                (cfg / "heuristic_shortcuts.local.json").write_text(
                    json.dumps(local, ensure_ascii=False), encoding="utf-8"
                )
                os.environ["GEMMA_PROJECT_ROOT"] = td
                registry_reload()
                rules = load_shortcut_rules()
                pats = rules["geo_nearby"].get("negative_patterns") or []
                self.assertEqual(pats, ["alpha", "beta"])
        finally:
            if old_root is None:
                os.environ.pop("GEMMA_PROJECT_ROOT", None)
            else:
                os.environ["GEMMA_PROJECT_ROOT"] = old_root
            registry_reload()

    def test_playbook_prose_hints_only_no_lane(self) -> None:
        story = (
            "день 1: баланс 1000, налог 13%. день 2: депозит 500, процент 5. "
            "итоговая оценка риска ликвидности по сценарию usd eur byn — таблица итераций "
            + "подробно " * 18
            + " посчитай итог"
        )
        ctx = TurnContext(user_text=story, intent="general")
        entry = match_situation(ctx)
        self.assertIsNotNone(entry)
        self.assertTrue(prose_blocks_playbook_lane(ctx))
        fc = forecast_pre_turn(ctx)
        self.assertFalse(getattr(fc, "situation_lane", None))
        self.assertTrue(fc.brain_hint_lines)

    def test_playbook_short_calc_still_sets_lane(self) -> None:
        ctx = TurnContext(user_text="посчитай 2+2", intent="general")
        fc = forecast_pre_turn(ctx)
        self.assertEqual(fc.situation_lane, "math_solve")

    def test_uncertain_llm_resolves_to_allowed(self) -> None:
        import asyncio

        ctx = build_turn_decision_context("что рядом с метро " + "x " * 40)
        os.environ["HEURISTIC_UNCERTAIN_LLM_ENABLED"] = "true"
        from core.heuristic_context_gate import GateResult

        mock_judge = AsyncMock(
            return_value=GateResult(verdict="allowed", rule_id="geo_nearby", reason="ok")
        )

        async def run() -> None:
            from core.heuristic_context_gate import should_run_shortcut_async

            with patch(
                "core.heuristic_uncertain_judge.judge_shortcut_uncertain",
                mock_judge,
            ):
                with patch(
                    "core.heuristic_context_gate.shortcut_allowed",
                    return_value=GateResult(
                        verdict="uncertain", rule_id="geo_nearby", reason="uncertain_prose"
                    ),
                ):
                    gr = await should_run_shortcut_async("geo_nearby", ctx.user_text)
                    self.assertTrue(gr.allowed)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
