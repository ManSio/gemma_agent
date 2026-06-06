import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.recovery_autonomy import (
    CriticalDataBackup,
    RecoveryAutonomyLayer,
    backup_before_critical_mutations,
    check_critical_integrity,
    resolve_bundle_id,
)


class RecoveryAutonomyTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        (self.root / "data" / "runtime").mkdir(parents=True)
        os.environ["PROJECT_ROOT"] = str(self.root)
        os.environ["AUTONOMY_BACKUP_ROOT"] = str(self.root / "bk")
        os.environ["AUTONOMY_LAYER_ENABLED"] = "true"
        os.environ["AUTONOMY_CRITICAL_PATHS"] = "data/pass.json,data/runtime/a.json"
        os.environ["DEVELOPMENT_PASSPORT_PATH"] = str(self.root / "data" / "pass.json")
        os.environ["RESILIENCE_RUNTIME_DIR"] = str(self.root / "data" / "runtime")

    def _write_files(self):
        (self.root / "data" / "pass.json").write_text(
            '{"mission": "m", "evolution_vectors": [], "priorities": [], "kpi_targets": {}, "stop_rules": []}',
            encoding="utf-8",
        )
        (self.root / "data" / "runtime" / "a.json").write_text('{"x": 1}', encoding="utf-8")

    def test_verify_accepts_backslash_manifest_keys(self):
        """Старые бэкапы с ключами data\\runtime\\... должны проходить verify на POSIX."""
        self._write_files()
        cb = CriticalDataBackup()
        r = cb.create_bundle(label="test")
        bundle = Path(r["path"])
        man = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        win_files = {}
        for k, v in (man.get("files") or {}).items():
            win_files[str(k).replace("/", "\\")] = v
        man["files"] = win_files
        (bundle / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        vr = cb.verify_bundle(bundle)
        self.assertTrue(vr.get("ok"), vr)

    def test_backup_verify_restore_roundtrip(self):
        self._write_files()
        cb = CriticalDataBackup()
        r = cb.create_bundle(label="test")
        self.assertTrue(r.get("ok"))
        bid = r["bundle_id"]
        vr = cb.verify_bundle(Path(r["path"]))
        self.assertTrue(vr.get("ok"))
        (self.root / "data" / "pass.json").write_text("{}", encoding="utf-8")
        rr = cb.restore_bundle(bid)
        self.assertTrue(rr.get("ok"))
        obj = json.loads((self.root / "data" / "pass.json").read_text(encoding="utf-8"))
        self.assertEqual(obj.get("mission"), "m")

    def test_integrity_detects_bad_json(self):
        self._write_files()
        (self.root / "data" / "pass.json").write_text("{", encoding="utf-8")
        rep = check_critical_integrity()
        self.assertFalse(rep["ok"])
        self.assertTrue(any("passport_corrupt" in x for x in rep["issues"]))

    def test_tick_periodic_backup(self):
        self._write_files()
        layer = RecoveryAutonomyLayer()
        orch = MagicMock()
        orch.get_system_info = MagicMock(return_value={"overall_status": "healthy", "modules": []})
        out = layer.tick(orch, maintenance_ran=True)
        self.assertTrue(out.get("ran"))
        self.assertIn("periodic_backup", out)

    def test_resolve_latest(self):
        self._write_files()
        CriticalDataBackup().create_bundle(label="one")
        bid = resolve_bundle_id("latest")
        self.assertTrue(bid and bid.startswith("backup_"))

    def test_backup_before_mutations_respects_disable(self):
        os.environ["AUTONOMY_LAYER_ENABLED"] = "false"
        self._write_files()
        r = backup_before_critical_mutations("x")
        self.assertTrue(r.get("skipped"))


if __name__ == "__main__":
    unittest.main()
