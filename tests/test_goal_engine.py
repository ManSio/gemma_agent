import unittest

from core.goal_engine import GoalEngine


class GoalEngineTests(unittest.TestCase):
    def test_update_and_hints(self):
        g = GoalEngine()
        state = g.load_state({})
        self.assertTrue(state["goals"])
        self.assertIn("mission", state)
        patch = g.update_after_turn(
            persisted={},
            user_text="need help with python refactor",
            assistant_text="ok",
        )
        self.assertIn("goals_long_term", patch)
        hints = g.planning_hints({"goals": patch["goals_long_term"]})
        self.assertIn("goal_ids", hints)
        self.assertTrue(hints["goal_ids"])
        full_hints = g.planning_hints(state)
        self.assertIn("evolution_vectors", full_hints)


if __name__ == "__main__":
    unittest.main()
