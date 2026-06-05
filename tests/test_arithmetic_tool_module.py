import unittest

from core.arithmetic_tool_module import ArithmeticToolModule, safe_eval_arithmetic


class ArithmeticToolModuleTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(safe_eval_arithmetic("2+2"), 4)
        self.assertEqual(safe_eval_arithmetic("(12+3)*4"), 60)

    def test_sqrt_pi(self):
        self.assertAlmostEqual(safe_eval_arithmetic("sqrt(2)"), 2**0.5)
        self.assertAlmostEqual(safe_eval_arithmetic("pi"), 3.141592653589793, places=6)
        self.assertAlmostEqual(safe_eval_arithmetic("sqrt(3^2 + 4^2)"), 5.0)

    def test_reject_import(self):
        with self.assertRaises(ValueError):
            safe_eval_arithmetic("__import__('os')")

    def test_tool_evaluate_ok(self):
        m = ArithmeticToolModule()
        out = m.evaluate("(1+2)*3")
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("result"), 9)

    def test_tool_evaluate_err(self):
        m = ArithmeticToolModule()
        out = m.evaluate("1/0")
        self.assertFalse(out.get("ok"))
        self.assertIn("error", out)

    def test_tool_evaluate_multi_newline(self):
        m = ArithmeticToolModule()
        out = m.evaluate("1+1\n2*3")
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("multi"))
        self.assertEqual(len(out.get("results")), 2)
        self.assertEqual(out["results"][0]["result"], 2)
        self.assertEqual(out["results"][1]["result"], 6)

    def test_tool_evaluate_multi_semicolon(self):
        m = ArithmeticToolModule()
        out = m.evaluate("(1362-1199)/1362*100; 1199*0.95")
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("multi"))
        self.assertEqual(len(out["results"]), 2)

    def test_tool_evaluate_multi_skips_garbage_lines(self):
        m = ArithmeticToolModule()
        raw = "🔥 скидки\n(1362-1199)/1362*100\nroome 1199 1362\n1199*0.95"
        out = m.evaluate(raw)
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("multi"))
        self.assertEqual(len(out["results"]), 2)

    def test_tool_evaluate_single_still_ok(self):
        m = ArithmeticToolModule()
        out = m.evaluate("sqrt(4)")
        self.assertTrue(out.get("ok"))
        self.assertNotIn("multi", out)
        self.assertEqual(out.get("result"), 2.0)


if __name__ == "__main__":
    unittest.main()
