import json
import os
import tempfile
import unittest
from unittest import mock

from core import development_passport as dp
from core.development_passport import (
    ensure_default_passport_file,
    get_development_passport,
    save_passport_patch,
    validate_passport_structure,
)


class DevelopmentPassportTests(unittest.TestCase):
    def test_default_passport_shape(self):
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "missing.json")
            with mock.patch.object(dp, "passport_file_path", return_value=missing):
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("DEVELOPMENT_PASSPORT_JSON", None)
                    p = get_development_passport()
        self.assertIn("mission", p)
        self.assertIn("evolution_vectors", p)
        self.assertIn("kpi_targets", p)
        self.assertIn("stop_rules", p)

    def test_file_overrides_env(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "passport.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"mission": "from_file"}, f)
            env = {
                "DEVELOPMENT_PASSPORT_PATH": path,
                "DEVELOPMENT_PASSPORT_JSON": json.dumps({"mission": "from_env"}),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                p = get_development_passport()
            self.assertEqual(p["mission"], "from_file")

    def test_ensure_default_passport_file_creates_once(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "passport.json")
            with mock.patch.object(dp, "passport_file_path", return_value=path):
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("DEVELOPMENT_PASSPORT_JSON", None)
                    ensure_default_passport_file()
                    self.assertTrue(os.path.isfile(path))
                    ensure_default_passport_file()
                with open(path, encoding="utf-8") as f:
                    obj = json.load(f)
            self.assertIn("mission", obj)

    def test_validate_rejects_unknown_key(self):
        with self.assertRaises(ValueError) as ctx:
            validate_passport_structure({"mission": "x", "extra": 1})
        self.assertIn("unknown", str(ctx.exception).lower())

    def test_validate_kpi_rejects_bool(self):
        with self.assertRaises(ValueError):
            validate_passport_structure({"kpi_targets": {"x": True}})

    def test_save_merge_kpi_partial(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "passport.json")
            env = {"DEVELOPMENT_PASSPORT_PATH": path}
            with mock.patch.dict(os.environ, env, clear=False):
                save_passport_patch({"kpi_targets": {"planner_fallback_total_max": 99}})
                p = get_development_passport()
            self.assertEqual(p["kpi_targets"]["planner_fallback_total_max"], 99)
            self.assertIn("security_high_risk_total_max", p["kpi_targets"])


if __name__ == "__main__":
    unittest.main()
