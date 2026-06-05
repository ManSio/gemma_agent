import os
import unittest
from unittest.mock import patch

from core.live_pulse import (
    _detect_anomalies,
    build_pulse_snapshot,
    build_xray_snapshot,
    p95_anomaly_thresholds_ms,
    record_planner_pulse,
    recent_planner_tail,
    xray_anomalies_for_display,
)

class _FakeRes:
    def is_enabled(self) -> bool:
        return True

    def is_safe_mode(self) -> bool:
        return False


class _FakeOrch:
    _resilience = _FakeRes()


class LivePulseTests(unittest.TestCase):
    def setUp(self) -> None:
        from core import live_pulse as lp

        with lp._lock:
            lp._planner_tail.clear()

    def test_record_and_snapshot(self):
        record_planner_pulse(
            intent="general",
            module="chat-orchestrator",
            fallback=False,
            reason="chat_orchestrator_fallback",
            skill_name="",
            trace_id="abc123",
            maintenance_ran=False,
            safe_mode=False,
        )
        rows = recent_planner_tail()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["module"], "chat-orchestrator")

        snap = build_pulse_snapshot(_FakeOrch())
        self.assertIn("monitoring", snap)
        self.assertIn("observability", snap)
        self.assertIn("heavy_worker", snap)
        self.assertEqual(len(snap["planner_recent"]), 1)

    def test_xray_contains_anomalies_field(self):
        snap = build_xray_snapshot(_FakeOrch())
        self.assertIn("pulse", snap)
        self.assertIn("errors", snap)
        self.assertIn("anomalies", snap)
        self.assertIn("usage_learning", snap)
        self.assertIn("usage_insights", snap)

    def test_autopilot_display_skips_warn_at_47s_p95(self):
        snap = {
            "observability": {"p95_ms": {"telegram_pipeline": 47270, "openrouter_completion": 7494}},
            "monitoring": {"openrouter_completion_ok_total": 20, "openrouter_completion_fail_total": 0},
            "heavy_worker": {},
            "host_resources": {"available": True, "pressure": {"level": "ok"}},
            "resilience": {},
            "boot": {},
        }
        with patch.dict(
            os.environ,
            {"LIVE_PULSE_TELEGRAM_P95_WARN_MS": "18000", "LIVE_PULSE_TELEGRAM_P95_CRITICAL_MS": "90000"},
            clear=False,
        ):
            xray = {"anomalies": _detect_anomalies(snap)}
        self.assertIn("telegram_p95_high", [a["code"] for a in xray["anomalies"]])
        self.assertEqual(xray_anomalies_for_display(xray, include_warn=False), [])

    def test_p95_thresholds_env_relaxed_no_anomaly_for_20s(self):
        minimal = {
            "observability": {"p95_ms": {"telegram_pipeline": 20000, "openrouter_completion": 16000}},
            "monitoring": {"openrouter_completion_ok_total": 0, "openrouter_completion_fail_total": 0},
            "heavy_worker": {},
            "host_resources": {},
            "resilience": {},
            "boot": {},
        }
        with patch.dict(
            os.environ,
            {
                "LIVE_PULSE_TELEGRAM_P95_WARN_MS": "25000",
                "LIVE_PULSE_TELEGRAM_P95_CRITICAL_MS": "35000",
                "LIVE_PULSE_OPENROUTER_P95_CRITICAL_MS": "35000",
            },
            clear=False,
        ):
            codes = [a["code"] for a in _detect_anomalies(minimal)]
        self.assertNotIn("telegram_p95_very_high", codes)
        self.assertNotIn("telegram_p95_high", codes)
        self.assertNotIn("openrouter_p95_very_high", codes)

    def test_p95_thresholds_ms_defaults(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("LIVE_PULSE_TELEGRAM") and k != "LIVE_PULSE_OPENROUTER_P95_CRITICAL_MS"}
        with patch.dict(os.environ, env, clear=True):
            t = p95_anomaly_thresholds_ms()
        self.assertEqual(t["telegram_warn_ms"], 18000.0)
        self.assertEqual(t["telegram_critical_ms"], 90000.0)
        self.assertEqual(t["openrouter_critical_ms"], 12000.0)

    def test_xray_anomalies_for_display_dedupes_event_bus_echo(self):
        xray = {
            "anomalies": [
                {
                    "severity": "warn",
                    "code": "telegram_p95_pipeline_slow",
                    "detail": "telegram_pipeline p95=47270ms openrouter_p95=7494ms",
                },
                {"code": "telegram_p95_pipeline_slow", "type": "event_bus", "severity": "warn"},
                {"code": "telegram_p95_pipeline_slow", "type": "event_bus", "severity": "warn"},
            ]
        }
        shown = xray_anomalies_for_display(xray)
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0]["detail"], "telegram_pipeline p95=47270ms openrouter_p95=7494ms")

    def test_telegram_slow_downgraded_when_llm_ok(self):
        snap = {
            "observability": {"p95_ms": {"telegram_pipeline": 95000, "openrouter_completion": 5000}},
            "monitoring": {"input_messages_total": 50, "openrouter_completion_ok_total": 20, "openrouter_completion_fail_total": 0},
            "heavy_worker": {},
            "host_resources": {"available": True, "pressure": {"level": "ok"}},
            "resilience": {},
            "boot": {},
        }
        with patch.dict(
            os.environ,
            {
                "LIVE_PULSE_TELEGRAM_P95_WARN_MS": "18000",
                "LIVE_PULSE_TELEGRAM_P95_CRITICAL_MS": "90000",
                "LIVE_PULSE_OPENROUTER_P95_CRITICAL_MS": "12000",
            },
            clear=False,
        ):
            codes = [a["code"] for a in _detect_anomalies(snap)]
        self.assertIn("telegram_p95_pipeline_slow", codes)
        self.assertNotIn("telegram_p95_very_high", codes)


if __name__ == "__main__":
    unittest.main()
