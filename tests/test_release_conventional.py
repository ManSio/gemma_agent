"""Парсинг conventional commits для авто-версий."""
from __future__ import annotations

import unittest

from core.release_conventional import (
    bump_semver,
    is_app_path,
    max_bump,
    parse_conventional_bump,
)


class TestReleaseConventional(unittest.TestCase):
    def test_feat_minor(self):
        self.assertEqual(parse_conventional_bump("feat: add x", ""), "minor")

    def test_fix_patch(self):
        self.assertEqual(parse_conventional_bump("fix: bug", ""), "patch")

    def test_breaking_body_major(self):
        self.assertEqual(parse_conventional_bump("feat: api", "BREAKING CHANGE: x"), "major")

    def test_bang_major(self):
        self.assertEqual(parse_conventional_bump("feat!: change", ""), "major")
        self.assertEqual(parse_conventional_bump("chore(api)!: drop", ""), "major")

    def test_docs_none(self):
        self.assertIsNone(parse_conventional_bump("docs: readme", ""))

    def test_unknown_none(self):
        self.assertIsNone(parse_conventional_bump("random message", ""))

    def test_max_bump(self):
        self.assertEqual(max_bump(["patch", "minor", "patch"]), "minor")
        self.assertEqual(max_bump(["patch", "major"]), "major")
        self.assertIsNone(max_bump([None, None]))

    def test_bump_semver(self):
        self.assertEqual(bump_semver("1.2.3", "patch"), "1.2.4")
        self.assertEqual(bump_semver("1.2.3", "minor"), "1.3.0")
        self.assertEqual(bump_semver("1.2.3", "major"), "2.0.0")
        self.assertEqual(bump_semver("1.0", "patch"), "1.0.1")
        self.assertEqual(bump_semver("2", "minor"), "2.1.0")
        self.assertEqual(bump_semver("2", "major"), "3.0.0")
        self.assertEqual(bump_semver("1.0.0-beta", "patch"), "1.0.1-beta")

    def test_is_app_path(self):
        self.assertTrue(is_app_path("core/foo.py"))
        self.assertTrue(is_app_path("main.py"))
        self.assertFalse(is_app_path("modules/rag/x.py"))
        self.assertFalse(is_app_path("core/__pycache__/x.pyc"))
