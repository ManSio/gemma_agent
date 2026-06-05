"""Доменные подсказки для мозга (память, RAG, право, …)."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from core.goal_domain_policy import format_domain_routing_addon, route_goal_domain


class GoalDomainPolicyTests(unittest.TestCase):
    def test_law_domain(self):
        r = route_goal_domain("Найди указ о поддержке жилья на law.example.com")
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "legal")

    def test_law_domain_general_base_documents(self):
        r = route_goal_domain("Найди в общей базе документ указ 95 от 2025")
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "legal")

    def test_law_domain_zakonakh_and_liability_phrase(self):
        r = route_goal_domain('найди всё про законах «ответственность за тишину»')
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "legal")

    def test_law_domain_local_base_followup(self):
        r = route_goal_domain("а в локальной базе?")
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "legal")

    def test_books_rag_domain(self):
        r = route_goal_domain("Покажи что в BooksRAG по физике")
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "books_rag")

    def test_user_memory_domain(self):
        r = route_goal_domain("Что сохранено в личной библиотеке")
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "user_memory")

    def test_user_memory_archive_notes_phrase(self):
        r = route_goal_domain(
            "Что у меня в архиве заметок и что в личной библиотеке файлов — перечисли отдельно"
        )
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "user_memory")

    def test_edu_portal_domain(self):
        r = route_goal_domain("Учебник математики с портала padruchnik-asabliva")
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "education")

    def test_general_when_disabled(self):
        with patch.dict("os.environ", {"GOAL_DOMAIN_POLICY_ENABLED": "false"}):
            r = route_goal_domain("что сохранено")
            self.assertIsNone(r)

    def test_addon_non_empty(self):
        s = format_domain_routing_addon("корпус книг rag")
        self.assertIn("books_rag", s)
        self.assertIn("BooksRAG", s)

    def test_multistep_boundary_addon(self):
        long_ms = (
            "Сначала найди три статьи про квантовые компьютеры, потом сравни выводы в двух абзацах"
        )
        with patch.dict(
            "os.environ",
            {"GOAL_RUNNER_ENABLED": "true", "GOAL_DOMAIN_MULTISTEP_HINT": "true"},
            clear=False,
        ):
            s = format_domain_routing_addon(long_ms)
            self.assertIn("goal_runner_boundary", s)
            self.assertIn("Goal Runner", s)

    def test_multistep_boundary_respects_env_off(self):
        long_ms = "Сначала сделай А потом Б и ещё что-нибудь про тесты длинная строка"
        with patch.dict(
            "os.environ",
            {
                "GOAL_RUNNER_ENABLED": "true",
                "GOAL_DOMAIN_MULTISTEP_HINT": "false",
            },
            clear=False,
        ):
            s = format_domain_routing_addon(long_ms)
            self.assertNotIn("goal_runner_boundary", s)


if __name__ == "__main__":
    unittest.main()
