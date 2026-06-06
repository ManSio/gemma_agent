import json
import os
import tempfile
import unittest
from unittest import mock

from core.brain.eta_estimate import estimate_llm_eta_sec
from core.llm_eta_learn import (
    blended_assembly_sec,
    blended_eta_sec,
    bucket_key,
    learn_assembly_sec,
    learn_from_llm_result,
    lookup_learned_tps,
)


class LlmEtaLearnTests(unittest.TestCase):
    def test_bucket_key_stable(self):
        self.assertEqual(
            bucket_key(tag="llm_first_stage", task_tier="deep", max_tokens=1536),
            "first|deep|t16",
        )

    def test_learn_then_blend(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "stats.json")
            with mock.patch.dict(
                os.environ,
                {
                    "GEMMA_LLM_ETA_STATS_PATH": path,
                    "BRAIN_LLM_ETA_LEARN_MIN_SAMPLES": "2",
                    "BRAIN_LLM_ETA_LEARN_BLEND_SPAN": "10",
                },
                clear=False,
            ):
                learn_from_llm_result(
                    {"content": "ok", "latency_ms": 10000.0},
                    tag="llm_first_stage",
                    task_tier="",
                    max_tokens=1536,
                    prompt="x",
                )
                learn_from_llm_result(
                    {"content": "ok2", "latency_ms": 20000.0},
                    tag="llm_first_stage",
                    task_tier="",
                    max_tokens=1536,
                    prompt="x",
                )
                h = 30.0
                b = blended_eta_sec(stage="first", task_tier="", max_tokens=1536, heuristic_sec=h)
                self.assertNotEqual(b, h)
                data = json.loads(open(path, encoding="utf-8").read())
                self.assertIn("buckets", data)
                self.assertGreaterEqual(len(data["buckets"]), 1)

    def test_learn_tps_from_completion(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "stats.json")
            with mock.patch.dict(
                os.environ,
                {
                    "GEMMA_LLM_ETA_STATS_PATH": path,
                    "BRAIN_LLM_ETA_LEARN_MIN_SAMPLES": "2",
                },
                clear=False,
            ):
                for _ in range(3):
                    learn_from_llm_result(
                        {
                            "content": "answer",
                            "latency_ms": 4000.0,
                            "usage_detail": {"completion_tokens": 200},
                        },
                        tag="llm_first_stage",
                        task_tier="",
                        max_tokens=1536,
                        prompt="prompt",
                    )
                tps = lookup_learned_tps(stage="first", task_tier="", max_tokens=1536)
                self.assertIsNotNone(tps)
                self.assertGreater(tps, 30.0)
                self.assertLess(tps, 80.0)

    def test_assembly_learn_blend(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "stats.json")
            with mock.patch.dict(
                os.environ,
                {
                    "GEMMA_LLM_ETA_STATS_PATH": path,
                    "BRAIN_LLM_ETA_ASSEMBLY_LEARN_MIN_SAMPLES": "2",
                },
                clear=False,
            ):
                learn_assembly_sec(8.0)
                learn_assembly_sec(10.0)
                learn_assembly_sec(9.0)
                asm = blended_assembly_sec()
                self.assertIsNotNone(asm)
                self.assertGreater(asm, 7.0)
                self.assertLess(asm, 12.0)


class EtaEstimateTests(unittest.TestCase):
    def test_longer_user_text_increases_eta(self):
        with mock.patch.dict(
            os.environ,
            {"BRAIN_LLM_TOKENS_PER_SEC_EST": "52", "BRAIN_LLM_ETA_LEARN_ENABLED": "false"},
            clear=False,
        ):
            short = estimate_llm_eta_sec(
                max_tokens=1536, task_tier="", prompt_len=2000, stage="first", user_text_len=40
            )
            long = estimate_llm_eta_sec(
                max_tokens=1536, task_tier="", prompt_len=2000, stage="first", user_text_len=12000
            )
        self.assertGreater(long, short)

    def test_prompt_len_boosts_eta(self):
        with mock.patch.dict(
            os.environ,
            {"BRAIN_LLM_TOKENS_PER_SEC_EST": "52", "BRAIN_LLM_ETA_LEARN_ENABLED": "false"},
            clear=False,
        ):
            small = estimate_llm_eta_sec(max_tokens=800, task_tier="", prompt_len=1000, stage="first")
            big = estimate_llm_eta_sec(max_tokens=800, task_tier="", prompt_len=20000, stage="first")
        self.assertGreater(big, small)

    def test_short_reply_not_using_full_max_tokens(self):
        with mock.patch.dict(
            os.environ,
            {"BRAIN_LLM_TOKENS_PER_SEC_EST": "52", "BRAIN_LLM_ETA_LEARN_ENABLED": "false"},
            clear=False,
        ):
            eta = estimate_llm_eta_sec(
                max_tokens=1536,
                task_tier="",
                prompt_len=5000,
                stage="first",
                user_text_len=20,
            )
        self.assertLess(eta, 12.0)


if __name__ == "__main__":
    unittest.main()
