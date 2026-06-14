"""Hot-path integration: Orchestrator plan → execute_plan → defer → finalize."""
from __future__ import annotations

import os
import secrets
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from core.behavior_store import BehaviorStore
from core.models import Input
from core.orchestrator import Orchestrator
from core.plugin_registry import PluginRegistry
from core.policy_engine import PolicyEngine
from core.turn_delivery_store import finalize_delivery_from_output_meta
from tests.support.turn_life_sim import (
    SimEpisode,
    TurnLifeSimulator,
    episode_seed,
    episode_summary,
    mock_reply_for_turn,
)

_TURN_ENV = {
    "TURN_CONTRACT_ENABLED": "true",
    "TURN_DEFER_STORE_ENABLED": "true",
    "ANTI_ECHO_GUARD_ENABLED": "true",
    "TURN_STICKY_LANE_ENABLED": "true",
    "TURN_CORRECTION_OVERRIDE_ENABLED": "true",
    "TURN_HASH_DRIFT_ENABLED": "true",
    "TURN_PROMPT_ADDITIVE_ENABLED": "true",
    "TURN_FINGERPRINT_ALERT_ENABLED": "true",
    "GOAL_RUNNER_ENABLED": "false",
}

_LIFE_EPISODE_COUNT = int(os.environ.get("TURN_LIFE_SIM_EPISODES", "6"))


def _step_meta(plan) -> dict:
    """Извлечь meta первого шага plan."""
    inp = (plan.steps[0].args or {}).get("input") or {}
    return inp.get("meta") if isinstance(inp, dict) else {}


class TurnHotPathIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Полный цикл orchestrator с BehaviorStore и TurnContract."""

    async def asyncSetUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self._env = patch.dict(os.environ, _TURN_ENV, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        self._planner_direct = patch(
            "core.brain_own_turn.planner_direct_allowed",
            return_value=True,
        )
        self._planner_direct.start()
        self.addCleanup(self._planner_direct.stop)
        self.store = BehaviorStore(base_dir=self._td.name)
        pr = PluginRegistry(self._td.name)
        pe = PolicyEngine()
        self.orch = Orchestrator(
            plugin_registry=pr,
            policy_engine=pe,
            behavior_store=self.store,
        )

    def _input(self, text: str, user_id: str, *, gen: int, trace: str) -> Input:
        """Собрать Input с generation token."""
        return Input(
            type="text",
            payload=text,
            meta={
                "trace_id": trace,
                "turn_generation": gen,
                "user_id": user_id,
            },
        )

    def _direct_mock_stack(self, stack: ExitStack, active: dict) -> None:
        """Patch direct shortcuts — ответ зависит от текущего хода эпизода."""

        def _wx(*_a, **_k):
            return active.get("reply") if active.get("kind") == "weather" else None

        def _math(*_a, **_k):
            return active.get("reply") if active.get("kind") == "math" else None

        def _news(*_a, **_k):
            return active.get("reply") if active.get("kind") == "news" else None

        stack.enter_context(patch("core.weather_reply.try_weather_reply_sync", side_effect=_wx))
        stack.enter_context(
            patch("core.referential_math_reply.try_referential_math_reply_sync", side_effect=_math)
        )
        stack.enter_context(patch("core.news_reply.try_news_reply_sync", side_effect=_news))

    async def _run_life_episode(self, episode: SimEpisode) -> None:
        """Прогнать случайный эпизод и проверить инварианты hot path."""
        uid = episode.user_id
        active: dict = {"kind": "", "reply": None}
        prev_gen = self.store.get_turn_generation(uid, None)
        msgs_before = len((self.store.load(uid, None) or {}).get("recent_messages") or [])

        with ExitStack() as stack:
            self._direct_mock_stack(stack, active)
            for idx, turn in enumerate(episode.turns):
                trace = f"life-{episode.seed}-{idx}"
                active["kind"] = turn.kind if mock_reply_for_turn(turn) else ""
                active["reply"] = turn.mock_reply

                if turn.inject_stale:
                    gen = self.store.bump_turn_generation(uid, None)
                    inp = self._input(turn.user_text, uid, gen=gen, trace=trace)
                    plan = self.orch.plan(inp, user_id=uid, group_id=None)
                    self.store.bump_turn_generation(uid, None)
                    outputs = await self.orch.execute_plan(plan, user_id=uid, group_id=None)
                    out_meta = outputs[0].meta or {}
                    self.assertIsNone(
                        out_meta.get("pending_turn_store"),
                        msg=f"stale pending {episode_summary(episode)} #{idx}",
                    )
                    continue

                gen = self.store.bump_turn_generation(uid, None)
                inp = self._input(turn.user_text, uid, gen=gen, trace=trace)
                plan = self.orch.plan(inp, user_id=uid, group_id=None)
                meta = _step_meta(plan)
                self.assertIsInstance(
                    meta.get("turn_contract"),
                    dict,
                    msg=f"plan tc {episode_summary(episode)} #{idx} kind={turn.kind}",
                )
                self.assertGreater(
                    int((meta.get("turn_contract") or {}).get("generation") or 0),
                    0,
                    msg=f"gen {episode_summary(episode)} #{idx}",
                )

                if turn.kind == "empty":
                    self.assertEqual(
                        (plan.steps[0].args or {}).get("fallback_variant"),
                        "empty_payload",
                    )

                outputs = await self.orch.execute_plan(plan, user_id=uid, group_id=None)
                self.assertGreaterEqual(len(outputs), 1)
                out_meta = dict(outputs[0].meta or {})
                self.assertIsInstance(
                    out_meta.get("turn_contract"),
                    dict,
                    msg=f"out tc {episode_summary(episode)} #{idx}",
                )
                self.assertEqual(out_meta.get("turn_generation"), gen)

                if turn.kind == "empty":
                    continue

                pending = out_meta.get("pending_turn_store")
                payload = str(outputs[0].payload or "")
                if isinstance(pending, dict) and payload.strip():
                    sent = turn.finalize_override or payload
                    ok = finalize_delivery_from_output_meta(
                        orchestrator=self.orch,
                        user_id=uid,
                        group_id=None,
                        sent_text=sent,
                        output_meta=out_meta,
                    )
                    self.assertTrue(ok, msg=f"finalize {episode_summary(episode)} #{idx}")
                    rec = self.store.load(uid, None)
                    self.assertEqual(
                        rec["recent_messages"][-1]["text"],
                        sent,
                        msg=f"store text {episode_summary(episode)} #{idx}",
                    )

                cur_gen = self.store.get_turn_generation(uid, None)
                self.assertGreaterEqual(cur_gen, prev_gen)
                prev_gen = cur_gen

        msgs_after = len((self.store.load(uid, None) or {}).get("recent_messages") or [])
        self.assertGreaterEqual(msgs_after, msgs_before)

    async def test_empty_fast_path_plan_execute_turn_contract(self) -> None:
        """Пустой payload: fast path + turn_contract в plan и output meta."""
        uid = "u_empty"
        gen = self.store.bump_turn_generation(uid, None)
        inp = Input(type="text", payload="   ", meta={"trace_id": "t-empty", "turn_generation": gen})
        plan = self.orch.plan(inp, user_id=uid, group_id=None)
        meta = _step_meta(plan)
        self.assertEqual((plan.steps[0].args or {}).get("fallback_variant"), "empty_payload")
        self.assertIsInstance(meta.get("turn_contract"), dict)
        self.assertTrue(str(meta.get("plan_turn_hash") or ""))
        outputs = await self.orch.execute_plan(plan, user_id=uid, group_id=None)
        self.assertEqual(len(outputs), 1)
        out_meta = outputs[0].meta or {}
        self.assertIsInstance(out_meta.get("turn_contract"), dict)
        self.assertEqual(out_meta.get("turn_generation"), gen)

    async def test_stale_generation_skips_store_on_execute(self) -> None:
        """Устаревший generation: execute не пишет в behavior_store."""
        uid = "u_stale"
        gen = self.store.bump_turn_generation(uid, None)
        inp = self._input("привет", uid, gen=gen, trace="stale-edge")
        plan = self.orch.plan(inp, user_id=uid, group_id=None)
        self.store.bump_turn_generation(uid, None)
        with patch("core.weather_reply.try_weather_reply_sync", return_value=None):
            outputs = await self.orch.execute_plan(plan, user_id=uid, group_id=None)
        out_meta = outputs[0].meta or {}
        self.assertIsNone(out_meta.get("pending_turn_store"))
        rec = self.store.load(uid, None)
        self.assertFalse(rec.get("recent_messages"))

    async def test_life_sim_random_episodes_invariants(self) -> None:
        """Несколько случайных эпизодов: разные ходы, generation, defer/finalize."""
        self.assertGreaterEqual(_LIFE_EPISODE_COUNT, 3)
        for i in range(_LIFE_EPISODE_COUNT):
            seed = episode_seed(f"life_invariants_{i}")
            sim = TurnLifeSimulator(seed)
            episode = sim.generate_episode(min_turns=6, max_turns=12)
            with self.subTest(episode=episode_summary(episode)):
                await self._run_life_episode(episode)

    async def test_life_sim_unique_paths_not_identical(self) -> None:
        """Эпизоды с разными seed не сводятся к одному шаблону."""
        paths: set[str] = set()
        for i in range(8):
            seed = episode_seed(f"life_unique_{i}")
            ep = TurnLifeSimulator(seed).generate_episode(min_turns=5, max_turns=9)
            paths.add("→".join(t.kind for t in ep.turns))
        self.assertGreaterEqual(len(paths), 4, "эпизоды должны различаться")

    @unittest.skipUnless(
        os.environ.get("TURN_LIFE_SIM_CHAOS") == "1",
        "Set TURN_LIFE_SIM_CHAOS=1 for non-deterministic chaos episode",
    )
    async def test_life_sim_chaos_episode(self) -> None:
        """Опциональный хаос-эпизод: seed неизвестен до запуска (локальная симуляция жизни)."""
        seed = secrets.randbits(32)
        episode = TurnLifeSimulator(seed).generate_episode(min_turns=8, max_turns=16)
        await self._run_life_episode(episode)


class TurnLifeSimUnitTests(unittest.TestCase):
    """Юнит-тесты генератора без orchestrator."""

    def test_direct_mock_targets_complete(self) -> None:
        """Все direct kind из симулятора имеют patch target."""
        for i in range(20):
            ep = TurnLifeSimulator(episode_seed(f"dm_{i}")).generate_episode(min_turns=3, max_turns=8)
            for turn in ep.turns:
                if turn.kind in ("weather", "math", "news"):
                    self.assertTrue(turn.mock_reply, msg=turn.kind)
                    self.assertIn(turn.kind, ("weather", "math", "news"))

    def test_episode_seed_override(self) -> None:
        """TURN_LIFE_SIM_SEED фиксирует replay."""
        with patch.dict(os.environ, {"TURN_LIFE_SIM_SEED": "424242"}, clear=False):
            self.assertEqual(episode_seed("any"), 424242)


if __name__ == "__main__":
    unittest.main()
