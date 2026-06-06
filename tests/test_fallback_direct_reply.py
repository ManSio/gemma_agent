import unittest

from core.models import PlanStep
from core.orchestrator import Orchestrator


class FallbackDirectReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_geo_nearby_direct_reply_not_ne_ponyal(self):
        orch = Orchestrator.__new__(Orchestrator)
        step = PlanStep(
            module_name="__fallback__",
            args={
                "fallback_variant": "geo_nearby",
                "direct_reply": "Рядом с вашей точкой:\n• Кафе",
            },
        )
        outs = await orch._execute_step(step, "1", None, 0)
        self.assertEqual(1, len(outs))
        self.assertIn("Рядом", outs[0].payload)
        self.assertNotIn("не понял", (outs[0].payload or "").lower())

    async def test_article_thread_sanitize_on_execute(self):
        from core.article_thread_followup import article_thread_honest_fallback_reply

        orch = Orchestrator.__new__(Orchestrator)
        leak = "Главные новости\n\n1. Крым\n\nНапишите номер пункта"
        step = PlanStep(
            module_name="__fallback__",
            args={
                "fallback_variant": "article_thread_followup_nl",
                "direct_reply": leak,
            },
        )
        outs = await orch._execute_step(step, "1", None, 0)
        self.assertEqual(1, len(outs))
        self.assertEqual(outs[0].payload, article_thread_honest_fallback_reply())
        self.assertEqual(outs[0].meta.get("reason"), "article_thread_followup_nl")

    async def test_article_thread_plain_header_not_html_leak(self):
        from core.article_thread_followup import sanitize_article_thread_direct_reply

        body = "Дополнительно по теме\nИран: новый раунд переговоров."
        outs = await Orchestrator.__new__(Orchestrator)._execute_step(
            PlanStep(
                module_name="__fallback__",
                args={
                    "fallback_variant": "article_thread_followup_nl",
                    "direct_reply": body,
                },
            ),
            "1",
            None,
            0,
        )
        self.assertNotIn("<b>", outs[0].payload or "")
        self.assertIn("Дополнительно", outs[0].payload or "")
        sanitized = sanitize_article_thread_direct_reply(
            "Главные новости\n\n1. Иран\n\nНапишите номер пункта"
        )
        self.assertNotIn("Главные новости", sanitized)


if __name__ == "__main__":
    unittest.main()
