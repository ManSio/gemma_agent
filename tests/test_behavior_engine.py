import unittest

from core.behavior_engine import BehaviorEngine


class BehaviorEngineTests(unittest.TestCase):
    def test_policy_derivation(self):
        be = BehaviorEngine()
        pol = be.derive_policy(
            persona={"name": "teacher_mode"},
            psychology={"anxiety_level": "high"},
            user_facts={"age": "14"},
            dialogue_state={"mode": "chat"},
        )
        self.assertEqual(pol["tone"], "supportive")
        self.assertEqual(pol["audience"], "teen")
        self.assertEqual(pol["mode"], "chat")


if __name__ == "__main__":
    unittest.main()
