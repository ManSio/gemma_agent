import json
import tempfile
import unittest
from pathlib import Path

from core.plugin_requirements import (
    collect_modules_pip_requirements,
    merge_plugin_requirements_report,
    merged_pip_requirements,
    requirement_distribution_key,
    runtime_pip_install_forbidden,
)


class PluginRequirementsTests(unittest.TestCase):
    def test_collect_and_merge(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mdir = root / "demo_mod"
            mdir.mkdir()
            (mdir / "module.json").write_text(
                json.dumps(
                    {
                        "name": "demo_mod",
                        "version": "1.0.0",
                        "type": "tool",
                        "entrypoint": "x:Y",
                        "pip_requirements": ["httpx>=0.27.0", "  ", "httpx>=0.27.0"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            by_mod = collect_modules_pip_requirements(root)
            key = f"{root.name}/demo_mod"
            self.assertEqual(by_mod.get(key), ["httpx>=0.27.0", "httpx>=0.27.0"])
            merged = merged_pip_requirements(root)
            self.assertEqual(merged, ["httpx>=0.27.0"])

    def test_distribution_key_normalization(self):
        self.assertEqual(requirement_distribution_key("httpx>=0.27"), "httpx")
        self.assertEqual(requirement_distribution_key("Some_Pkg~=1.0"), "some-pkg")

    def test_merge_conflict_same_distribution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "bundle"
            root.mkdir(parents=True)
            for name, spec in (("mod_a", "httpx>=1.0"), ("mod_b", "httpx>=2.0")):
                d = root / name
                d.mkdir()
                (d / "module.json").write_text(
                    json.dumps(
                        {
                            "name": name,
                            "type": "tool",
                            "entrypoint": "x:Y",
                            "pip_requirements": [spec],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            report = merge_plugin_requirements_report([root])
            self.assertEqual(len(report.duplicate_distribution_keys), 1)
            self.assertEqual(len(report.merged_lines), 1)

    def test_runtime_install_forbidden_flag(self):
        self.assertTrue(runtime_pip_install_forbidden())


if __name__ == "__main__":
    unittest.main()
