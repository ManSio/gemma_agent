import unittest

from core.report_i18n import format_kv_table_pre, format_metrics_table_pre, system_status_lamp


class ReportI18nLayoutTests(unittest.TestCase):
    def test_metrics_table_has_separator_and_right_align(self):
        s = format_metrics_table_pre(
            [("Мозг (LLM)", 26), ("Антифлуд", 9), ("Устойчивость", 5)],
            label_max=22,
            num_width=6,
        )
        self.assertIn("│", s)
        lines = s.split("\n")
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].rstrip().endswith("26"))
        self.assertTrue(lines[1].rstrip().endswith("9"))
        self.assertTrue(lines[2].rstrip().endswith("5"))

    def test_kv_table_separator(self):
        s = format_kv_table_pre([("Ключ", "значение"), ("A", "1")], label_max=10, value_max=10)
        self.assertIn("│", s)
        self.assertIn("Ключ", s)

    def test_kv_values_not_right_padded_to_longest(self):
        """Короткое значение сразу после │, без разъезда из‑за длинной строки в другой строке."""
        s = format_kv_table_pre(
            [("N", "49"), ("Статус", "всё в норме (healthy)")],
            label_max=12,
            value_max=40,
        )
        self.assertIn("│ 49", s.split("\n")[0])
        self.assertNotRegex(s.split("\n")[0], r"│\s{8,}49")


class SystemStatusLampTests(unittest.TestCase):
    def test_healthy_green(self):
        class O:
            def get_system_info(self):
                return {"overall_status": "healthy"}

        self.assertEqual(system_status_lamp(O()), "🟢")

    def test_degraded_red(self):
        class O:
            def get_system_info(self):
                return {"overall_status": "degraded"}

        self.assertEqual(system_status_lamp(O()), "🔴")


if __name__ == "__main__":
    unittest.main()
