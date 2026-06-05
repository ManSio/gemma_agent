import unittest

from core.llm_refusal import looks_model_refusal
from core.llm_tiered import _result_usable


class TestLlmRefusal(unittest.TestCase):
    def test_detects_short_refusal(self):
        self.assertTrue(looks_model_refusal("Извините, но я не могу помочь с этим запросом."))

    def test_normal_answer_not_refusal(self):
        self.assertFalse(looks_model_refusal("После родов либидо часто снижается из‑за гормонов и усталости."))

    def test_result_usable_rejects_refusal(self):
        self.assertFalse(
            _result_usable({"content": "Извините, но я не могу помочь с этим запросом."})
        )
        self.assertTrue(_result_usable({"content": "Кратко: восстановление занимает недели."}))


if __name__ == "__main__":
    unittest.main()
