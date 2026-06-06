"""Стабильность эвристик на тех же текстах, что ручной бенчмарк docs/archive/BOT_LOGIC_BENCHMARK_3_RU.md."""

import unittest

from core.brain.text_helpers import is_bot_operational_diag_question
from core.intent_heuristics import explicit_math_request, is_system_operator_directive, naive_math_intent_from_text
from core.intent_heuristics import strip_urls_and_mentions_for_math_probe

from tests.fixtures.logic_benchmark_prompts import (
    TEST1_COMPOUND_GROWTH,
    TEST1_REFERENCE_END_DAY5_USD,
    TEST2_CONTRADICTION,
    TEST3A_MEMORY_SET,
    TEST3B_MEMORY_RECALL,
    TEST3_EXPECTED_SUBSTRING,
)


class LogicBenchmarkPromptsTests(unittest.TestCase):
    def test_reference_constant_sanity(self):
        self.assertEqual(TEST1_REFERENCE_END_DAY5_USD, 5120)

    def test_b1_not_operational_diag_short_circuit(self):
        self.assertFalse(is_bot_operational_diag_question(TEST1_COMPOUND_GROWTH))

    def test_b1_not_explicit_math_for_calculator_router(self):
        """Длинный B1 не должен считаться явным «посчитай» (в отличие от короткого 2+2)."""
        scrub = strip_urls_and_mentions_for_math_probe(TEST1_COMPOUND_GROWTH)
        self.assertFalse(explicit_math_request(TEST1_COMPOUND_GROWTH, scrub))

    def test_b2_not_treated_as_system_directive_but_not_obvious_math_blob(self):
        self.assertFalse(is_system_operator_directive(TEST2_CONTRADICTION))
        self.assertFalse(naive_math_intent_from_text(TEST2_CONTRADICTION))

    def test_b3_strings_for_manual_run(self):
        self.assertIn("логика", TEST3_EXPECTED_SUBSTRING.lower())
        self.assertIn("B3", TEST3A_MEMORY_SET)
        self.assertIn("предыдущем", TEST3B_MEMORY_RECALL.lower())


if __name__ == "__main__":
    unittest.main()
