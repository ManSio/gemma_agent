import unittest
from unittest.mock import patch

from core import host_resources as hr
from core.host_resources import (
    _apply_cpu_boot_grace,
    _evaluate_pressure,
    _snapshot_during_metrics_boot_delay,
    get_host_resource_snapshot,
    resource_pressure_is_critical,
)


class HostResourcesTests(unittest.TestCase):
    def test_evaluate_pressure_ok(self):
        p = _evaluate_pressure(10.0, 40.0, [{"used_percent": 50.0, "path": "/"}])
        self.assertEqual(p["level"], "ok")

    def test_evaluate_pressure_warn_mem(self):
        p = _evaluate_pressure(10.0, 90.0, [])
        self.assertIn(p["level"], ("warn", "critical"))

    def test_cpu_only_critical_downgraded_in_boot_grace(self):
        with patch.object(hr, "_in_cpu_boot_grace", return_value=True):
            p = _apply_cpu_boot_grace({"level": "critical", "reasons": ["cpu_critical:100.0"]})
        self.assertEqual(p["level"], "warn")
        self.assertTrue(any("boot_grace" in r for r in p["reasons"]))

    def test_cpu_critical_not_downgraded_after_boot_grace(self):
        with patch.object(hr, "_in_cpu_boot_grace", return_value=False):
            p = _apply_cpu_boot_grace({"level": "critical", "reasons": ["cpu_critical:100.0"]})
        self.assertEqual(p["level"], "critical")

    def test_resource_pressure_is_critical_false_when_host_ok(self):
        with patch(
            "core.host_resources.get_host_resource_snapshot",
            return_value={"pressure": {"level": "ok", "reasons": []}},
        ):
            self.assertFalse(resource_pressure_is_critical())

    def test_snapshot_without_psutil(self):
        with patch("core.host_resources.psutil", None):
            s = get_host_resource_snapshot(force=True)
            self.assertFalse(s.get("available"))
            self.assertEqual(s.get("error"), "psutil_not_installed")

    def test_metrics_boot_delay_forces_ok_pressure(self):
        with patch.object(hr, "_in_metrics_boot_delay", return_value=True):
            s = _snapshot_during_metrics_boot_delay()
        self.assertTrue(s.get("metrics_boot_delay_active"))
        self.assertEqual(s.get("pressure", {}).get("level"), "ok")
        self.assertIsNone(s.get("cpu_percent"))

    def test_get_snapshot_skips_cpu_during_boot_delay(self):
        with patch.object(hr, "_in_metrics_boot_delay", return_value=True):
            with patch("core.host_resources._read_cpu_percent") as rd:
                s = get_host_resource_snapshot(force=True)
        rd.assert_not_called()
        self.assertEqual(s.get("pressure", {}).get("level"), "ok")


if __name__ == "__main__":
    unittest.main()
