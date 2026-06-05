import os
import unittest

from core.meta_intent_probe import (
    apply_meta_intent_pack,
    classify_meta_intent_heuristic,
    dialogue_review_from_meta,
    merge_meta_intent,
)


class MetaIntentProbeTests(unittest.TestCase):
    def test_heuristic_feedback(self):
        h = classify_meta_intent_heuristic("Ты сьехал с темы, вернись")
        self.assertEqual(h.get("meta"), "user_feedback")

    def test_heuristic_dialogue_review(self):
        h = classify_meta_intent_heuristic("Проверь переписку и скажи кто прав")
        self.assertEqual(h.get("meta"), "dialogue_review")

    def test_merge_prefers_llm_when_confident(self):
        heur = {"meta": "none", "confidence": 0.5, "source": "heuristic"}
        llm = {"meta": "user_feedback", "confidence": 0.82, "source": "llm"}
        os.environ["META_INTENT_MIN_CONFIDENCE"] = "0.5"
        m = merge_meta_intent(heur, llm)
        self.assertEqual(m.get("meta"), "user_feedback")
        self.assertEqual(m.get("source"), "llm")

    def test_merge_falls_back_to_heuristic(self):
        heur = {"meta": "user_feedback", "confidence": 0.82, "source": "heuristic"}
        llm = {"meta": "none", "confidence": 0.9, "source": "llm"}
        os.environ["META_INTENT_MIN_CONFIDENCE"] = "0.5"
        m = merge_meta_intent(heur, llm)
        self.assertEqual(m.get("meta"), "user_feedback")

    def test_dialogue_review_from_meta(self):
        os.environ["META_INTENT_MIN_CONFIDENCE"] = "0.5"
        self.assertTrue(
            dialogue_review_from_meta({"meta_intent": {"meta": "dialogue_review", "confidence": 0.7}})
        )
        self.assertFalse(dialogue_review_from_meta({"meta_intent": {"meta": "dialogue_review", "confidence": 0.2}}))

    def test_apply_pack_sets_context(self):
        ctx: dict = {"recent_dialogue": []}
        pack = {"meta": "user_feedback", "confidence": 0.9, "source": "llm"}
        apply_meta_intent_pack(
            ctx,
            pack,
            user_text="не то ответил",
            user_id=None,
            group_id=None,
            routing_prefs={},
            input_obj={},
        )
        self.assertEqual(ctx.get("meta_intent"), pack)
        self.assertIn("Обратная связь", str(ctx.get("user_remark_hint") or ""))


if __name__ == "__main__":
    unittest.main()
