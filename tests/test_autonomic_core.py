import json
import os
import unittest
from unittest.mock import patch

from datetime import datetime, timedelta, timezone

from core.experience_memory import fingerprint
from core.route_risk_memory import (
    build_route_risk_hint,
    classify_error_type,
    loose_bucket_fingerprint,
    record_stumble,
    route_risk_hint_enabled,
)
from core.session_digest import record_turn, reset_session_digest_buffers
from core.cost_controller import build_cost_autopilot_patch


class AutonomicCoreTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_session_digest_buffers()

    def test_session_digest_flushes_on_n(self):
        p = os.path.join(os.path.dirname(__file__), "_tmp_session_digest.jsonl")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        with patch.dict(
            os.environ,
            {
                "GEMMA_SESSION_DIGEST_PATH": p,
                "SESSION_DIGEST_ENABLED": "true",
                "SESSION_DIGEST_EVERY_N_TURNS": "3",
                "SESSION_DIGEST_MIN_USER_CHARS": "1",
            },
            clear=False,
        ):
            record_turn(user_id="u1", user_text="aa", outcome="ok", intent="general", module="chat")
            record_turn(user_id="u1", user_text="bb", outcome="ok", intent="general", module="chat")
            self.assertFalse(os.path.isfile(p))
            record_turn(user_id="u1", user_text="cc", outcome="fallback", intent="general", module="x")
        self.assertTrue(os.path.isfile(p))
        with open(p, "r", encoding="utf-8") as f:
            row = json.loads(f.readline())
        self.assertEqual(row.get("turns"), 3)
        self.assertEqual(row.get("ok"), 2)
        self.assertEqual(row.get("fallback"), 1)

    def test_route_risk_hint_after_repeats(self):
        p = os.path.join(os.path.dirname(__file__), "_tmp_route_risk.jsonl")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        txt = "уникальная формулировка для риска маршрута"
        fp = fingerprint(txt)
        self.assertTrue(fp)
        with patch.dict(os.environ, {"GEMMA_ROUTE_RISK_PATH": p, "ROUTE_RISK_MEMORY_ENABLED": "true"}, clear=False):
            record_stumble(
                user_text=txt,
                intent="general",
                module="chat",
                outcome="fallback",
                detail="default",
                path=p,
            )
            h1 = build_route_risk_hint(user_text=txt, intent="general", path=p)
            self.assertEqual(h1, "")
            record_stumble(
                user_text=txt,
                intent="general",
                module="chat",
                outcome="fallback",
                detail="default",
                path=p,
            )
            h2 = build_route_risk_hint(user_text=txt, intent="general", path=p)
        self.assertIn("стратег", h2.lower())

    def test_route_risk_ttl_excludes_stale_records(self):
        p = os.path.join(os.path.dirname(__file__), "_tmp_route_risk_ttl.jsonl")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        txt = "фраза для ttl теста маршрута"
        fp = fingerprint(txt)
        bucket = loose_bucket_fingerprint(txt)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        rec = {
            "ts": old_ts,
            "fp": fp,
            "bucket_fp": bucket,
            "intent": "general",
            "module": "chat",
            "outcome": "fallback",
            "detail": "",
            "severity": 2,
        }
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with patch.dict(
            os.environ,
            {
                "GEMMA_ROUTE_RISK_PATH": p,
                "ROUTE_RISK_HINT_ENABLED": "true",
                "ROUTE_RISK_HINT_TTL_SEC": "604800",
            },
            clear=False,
        ):
            h = build_route_risk_hint(user_text=txt, intent="general", path=p)
        self.assertEqual(h, "")

    def test_route_risk_strong_intro_after_errors(self):
        p = os.path.join(os.path.dirname(__file__), "_tmp_route_risk_strong.jsonl")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        txt = "запрос с историей ошибок api"
        fp = fingerprint(txt)
        bucket = loose_bucket_fingerprint(txt)
        now = datetime.now(timezone.utc).isoformat()
        line = {
            "ts": now,
            "fp": fp,
            "bucket_fp": bucket,
            "intent": "general",
            "module": "x",
            "outcome": "error",
            "detail": "timeout",
            "severity": 3,
        }
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            f.write(json.dumps({**line, "detail": "retry"}, ensure_ascii=False) + "\n")
        with patch.dict(os.environ, {"GEMMA_ROUTE_RISK_PATH": p, "ROUTE_RISK_HINT_ENABLED": "true"}, clear=False):
            h = build_route_risk_hint(user_text=txt, intent="general", path=p)
        self.assertIn("важно", h.lower())

    def test_route_risk_cluster_bucket_match(self):
        p = os.path.join(os.path.dirname(__file__), "_tmp_route_risk_cluster.jsonl")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        base = "общий префикс для кластера " + ("z" * 100)
        t1 = base + "-variant-a"
        t2 = base + "-variant-b"
        self.assertNotEqual(fingerprint(t1), fingerprint(t2))
        self.assertEqual(loose_bucket_fingerprint(t1), loose_bucket_fingerprint(t2))
        with patch.dict(os.environ, {"GEMMA_ROUTE_RISK_PATH": p, "ROUTE_RISK_MEMORY_ENABLED": "true"}, clear=False):
            record_stumble(user_text=t1, intent="general", module="chat", outcome="fallback", detail="x", path=p)
            record_stumble(user_text=t1, intent="general", module="chat", outcome="fallback", detail="y", path=p)
        with patch.dict(
            os.environ,
            {
                "GEMMA_ROUTE_RISK_PATH": p,
                "ROUTE_RISK_HINT_ENABLED": "true",
                "ROUTE_RISK_CLUSTER_MATCH": "true",
            },
            clear=False,
        ):
            h = build_route_risk_hint(user_text=t2, intent="general", path=p)
        self.assertIn("стратег", h.lower())

    def test_route_risk_error_taxonomy(self):
        self.assertEqual(classify_error_type(outcome="clarify", detail="math_ambiguous", module="chat"), "user_input")
        self.assertEqual(classify_error_type(outcome="error", detail="tool timeout", module="chat"), "tool")
        self.assertEqual(classify_error_type(outcome="fallback", detail="planner route", module="__fallback__"), "router")

    def test_route_risk_hint_when_memory_write_disabled(self):
        """Hint читает jsonl даже при ROUTE_RISK_MEMORY_ENABLED=false."""
        p = os.path.join(os.path.dirname(__file__), "_tmp_route_risk_hint_only.jsonl")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        txt = "только чтение журнала риска маршрута"
        fp = fingerprint(txt)
        self.assertTrue(fp)
        rec = {
            "fp": fp,
            "intent": "general",
            "module": "chat",
            "outcome": "fallback",
            "detail": "",
        }
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with patch.dict(
            os.environ,
            {
                "GEMMA_ROUTE_RISK_PATH": p,
                "ROUTE_RISK_MEMORY_ENABLED": "false",
                "ROUTE_RISK_HINT_ENABLED": "true",
            },
            clear=False,
        ):
            self.assertTrue(route_risk_hint_enabled())
            h = build_route_risk_hint(user_text=txt, intent="general", path=p)
        self.assertIn("стратег", h.lower())

    def test_fatigue_policy(self):
        from core.autonomic_fatigue import apply_fatigue_to_policy

        with patch("core.autonomic_fatigue.fatigue_should_force_slim", return_value=True):
            bp, slim = apply_fatigue_to_policy({"verbosity": "rich"})
        self.assertTrue(slim)
        self.assertEqual(bp.get("verbosity"), "concise")

    def test_cost_autopilot_normal_when_budget_not_reached(self):
        with patch.dict(
            os.environ,
            {
                "COST_AUTOPILOT_ENABLED": "true",
                "COST_DAILY_TOKEN_BUDGET": "1000",
                "COST_SAVING_THRESHOLD": "0.8",
                "COST_HARD_SAVING_THRESHOLD": "0.95",
            },
            clear=False,
        ):
            with patch("core.cost_controller._today_tokens_spent", return_value=120):
                p = build_cost_autopilot_patch(
                    user_text="привет",
                    planned_intent="general",
                    planned_module="chat-orchestrator",
                    predictive_hint={"confidence": 0.2},
                    has_rich_context=False,
                )
        self.assertEqual(p.get("mode"), "normal")
        self.assertNotIn("force_verbosity", p)

    def test_cost_autopilot_saving_caps_short_general(self):
        with patch.dict(
            os.environ,
            {
                "COST_AUTOPILOT_ENABLED": "true",
                "COST_DAILY_TOKEN_BUDGET": "1000",
                "COST_SAVING_THRESHOLD": "0.7",
                "COST_HARD_SAVING_THRESHOLD": "0.9",
            },
            clear=False,
        ):
            with patch("core.cost_controller._today_tokens_spent", return_value=780):
                p = build_cost_autopilot_patch(
                    user_text="как дела?",
                    planned_intent="general",
                    planned_module="chat-orchestrator",
                    predictive_hint={"confidence": 0.2},
                    has_rich_context=False,
                )
        self.assertEqual(p.get("mode"), "saving")
        self.assertEqual(p.get("force_verbosity"), "concise")
        self.assertEqual(p.get("task_tier_ceiling"), "nested")
        self.assertTrue(p.get("disable_strategy_hint"))

    def test_cost_autopilot_hard_saving_locks_down_short_chat(self):
        with patch.dict(
            os.environ,
            {
                "COST_AUTOPILOT_ENABLED": "true",
                "COST_DAILY_TOKEN_BUDGET": "1000",
                "COST_SAVING_THRESHOLD": "0.7",
                "COST_HARD_SAVING_THRESHOLD": "0.9",
            },
            clear=False,
        ):
            with patch("core.cost_controller._today_tokens_spent", return_value=950):
                p = build_cost_autopilot_patch(
                    user_text="ок",
                    planned_intent="general",
                    planned_module="chat-orchestrator",
                    predictive_hint={"confidence": 0.2},
                    has_rich_context=False,
                )
        self.assertEqual(p.get("mode"), "hard_saving")
        self.assertEqual(p.get("task_tier_ceiling"), "shallow")
        self.assertTrue(p.get("disable_experience_hint"))
        self.assertTrue(p.get("disable_route_risk_hint"))
        self.assertTrue(p.get("disable_tools"))


if __name__ == "__main__":
    unittest.main()
