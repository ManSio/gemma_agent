import unittest

from core.predictive_behavior_engine import PredictiveBehaviorEngine


class PredictiveBehaviorEngineTests(unittest.TestCase):
    def test_predict_code_bias(self):
        e = PredictiveBehaviorEngine()
        out = e.predict(
            text="fix python code bug quickly",
            recent_dialogue=[{"role": "user", "text": "code review"}],
            topic_tracking={"current": "code cleanup"},
            psychology={"anxiety_level": "low"},
            user_facts={},
        )
        self.assertIn("programmer", out["skill_priority"])
        self.assertGreater(out["confidence"], 0.3)


if __name__ == "__main__":
    unittest.main()
