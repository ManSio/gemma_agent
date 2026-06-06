"""Тесты Code Evolution (Фаза 9)."""
import ast
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.code_evolution import (
    CodePatch,
    OptimizationTarget,
    EvolutionLog,
    PatchRunner,
    AutoOptimizer,
    get_evolution_log,
)


class TestCodePatch(unittest.TestCase):
    def test_defaults(self):
        p = CodePatch(id="p1", ts=100.0, generated_by="auto_optimizer",
                      target_file="core/test.py", diff_text="test",
                      description="test patch", reason="reason")
        self.assertEqual(p.status, "pending")
        self.assertFalse(p.test_ok)
        self.assertFalse(p.deploy_ok)


class TestOptimizationTarget(unittest.TestCase):
    def test_defaults(self):
        t = OptimizationTarget(file_path="core/test.py", func_name="foo",
                               p95_ms=5000.0, call_count=10)
        self.assertEqual(t.file_path, "core/test.py")
        self.assertEqual(t.p95_ms, 5000.0)


class TestEvolutionLog(unittest.TestCase):
    def setUp(self):
        self.log = EvolutionLog()

    def test_record_and_recent(self):
        self.log.record("test_event", {"key": "val"})
        rec = self.log.recent(limit=5)
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["event_type"], "test_event")
        self.assertEqual(rec[0]["details"]["key"], "val")

    def test_recent_ordered(self):
        now = time.time()
        self.log.record("e1", {"ts": now - 10})
        self.log.record("e2", {"ts": now})
        rec = self.log.recent(limit=5)
        self.assertEqual(len(rec), 2)
        self.assertEqual(rec[0]["event_type"], "e2")  # newest first


class TestPatchRunner(unittest.TestCase):
    def setUp(self):
        self.log = EvolutionLog()
        self.runner = PatchRunner(self.log)
        # Создаём тестовые файлы
        self._files_created: list[Path] = []

    def tearDown(self):
        for fp in self._files_created:
            try:
                fp.unlink(missing_ok=True)
            except Exception:
                pass
        self._files_created.clear()

    def _create_test_file(self, rel_path: str, content: str = "x=1\n") -> Path:
        fp = Path(self.runner._project_root()) / rel_path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        self._files_created.append(fp)
        return fp

    def test_generate_patch_rejects_non_allowed(self):
        patch = self.runner.generate_patch(
            source_code="x=1",
            fix_description="test",
            target_file="etc/passwd",
        )
        self.assertIsNone(patch)

    def test_generate_patch_creates_patch(self):
        test_file = "core/test_gen_module.py"
        self._create_test_file(test_file, "x=1\n")
        patch = self.runner.generate_patch(
            source_code="x=2\n",
            fix_description="test fix",
            target_file=test_file,
        )
        self.assertIsNotNone(patch)
        self.assertEqual(patch.target_file, test_file)
        self.assertEqual(patch.status, "pending")
        self.assertIn("@@", patch.diff_text)

    def test_generate_patch_no_changes(self):
        test_file = "core/test_nochange.py"
        self._create_test_file(test_file, "x=1\n")
        patch = self.runner.generate_patch(
            source_code="x=1\n",
            fix_description="no change",
            target_file=test_file,
        )
        self.assertIsNone(patch)

    def test_apply_patch_skips_non_pending(self):
        patch = CodePatch(id="p_skip", ts=time.time(), generated_by="manual",
                          target_file="core/test_skip.py", diff_text="x",
                          description="skip", reason="skip", status="applied")
        ok = self.runner.apply_patch(patch)
        self.assertFalse(ok)

    def test_apply_patch_nonexistent_file(self):
        patch = CodePatch(id="p_nf", ts=time.time(), generated_by="manual",
                          target_file="core/nonexistent_file_xyz.py",
                          diff_text="x=1", description="nf", reason="nf")
        ok = self.runner.apply_patch(patch)
        self.assertFalse(ok)
        self.assertEqual(patch.status, "failed")

    def test_list_patches(self):
        test_file = "core/test_list_patch.py"
        self._create_test_file(test_file, "x=1\n")
        self.runner.generate_patch(
            source_code="x=2\n",
            fix_description="list test",
            target_file=test_file,
        )
        patches = self.runner.list_patches()
        self.assertGreaterEqual(len(patches), 1)

    def test_run_tests_skips_wrong_status(self):
        patch = CodePatch(id="p_skip2", ts=time.time(), generated_by="manual",
                          target_file="core/test.py", diff_text="x",
                          description="skip", reason="skip", status="applied")
        ok = self.runner.run_tests(patch)
        self.assertFalse(ok)

    def test_rollback_unknown(self):
        ok = self.runner.rollback("nonexistent")
        self.assertFalse(ok)

    def test_ast_optimize_time_sleep_in_async(self):
        source = (
            "import asyncio\n"
            "async def foo():\n"
            "    time.sleep(1)\n"
        )
        tree = ast.parse(source)
        result = PatchRunner._ast_optimize(tree, source)
        self.assertIn("await asyncio.sleep(1)", result)
        self.assertNotIn("time.sleep(", result)

    def test_ast_optimize_bare_except(self):
        source = (
            "try:\n"
            "    x = 1\n"
            "except:\n"
            "    pass\n"
        )
        tree = ast.parse(source)
        result = PatchRunner._ast_optimize(tree, source)
        self.assertIn("except Exception:", result)
        self.assertNotIn("except:\n", result)

    def test_ast_optimize_no_changes(self):
        source = "x = 1\n"
        tree = ast.parse(source)
        result = PatchRunner._ast_optimize(tree, source)
        self.assertEqual(result, source)


class TestAutoOptimizer(unittest.TestCase):
    def setUp(self):
        self.log = EvolutionLog()
        self.runner = PatchRunner(self.log)
        self.optimizer = AutoOptimizer(self.runner, self.log)

    def test_find_slow_by_error_rate_no_data(self):
        targets = self.optimizer._find_slow_by_error_rate()
        self.assertIsInstance(targets, list)

    def test_tick_skipped_on_interval(self):
        # _CODE_EVOL_OPTIMIZER_INTERVAL=720, counter=1 → skip
        self.optimizer._tick_counter = 1
        with patch.object(self.optimizer, "_find_slow_functions") as mock:
            self.optimizer.tick()
            mock.assert_not_called()


class TestGlobals(unittest.TestCase):
    def test_get_evolution_log(self):
        log1 = get_evolution_log()
        log2 = get_evolution_log()
        self.assertIs(log1, log2)


if __name__ == "__main__":
    unittest.main()
