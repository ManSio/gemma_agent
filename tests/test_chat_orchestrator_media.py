import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from modules.chat_orchestrator.module import ChatOrchestratorModule


class ChatOrchestratorMediaTests(unittest.TestCase):
    def test_photo_without_caption_calls_brain_with_synthetic_prompt(self):
        mod = ChatOrchestratorModule()

        async def _run() -> None:
            with patch(
                "modules.chat_orchestrator.module.call_brain",
                new_callable=AsyncMock,
                return_value="На фото кошка.",
            ) as m_brain:
                out = await mod.execute(
                    {
                        "input": {"payload": "", "type": "image"},
                        "context": {
                            "user_id": "1",
                            "file_context": {
                                "file_type": "image",
                                "local_path": "/tmp/test.jpg",
                            },
                        },
                    }
                )
                m_brain.assert_awaited_once()
                ut = (m_brain.call_args.kwargs or {}).get("user_text") or (
                    m_brain.call_args.args[0] if m_brain.call_args.args else ""
                )
                self.assertIn("фото", ut.lower())
                self.assertIn("кошка", (out.payload or ""))

        asyncio.run(_run())

    def test_empty_payload_no_image_is_rejected(self):
        mod = ChatOrchestratorModule()

        async def _run() -> None:
            out = await mod.execute(
                {
                    "input": {"payload": "  ", "type": "text"},
                    "context": {"user_id": "1"},
                }
            )
            self.assertIn("Пустой запрос", out.payload or "")

        asyncio.run(_run())

    def test_pdf_without_caption_calls_brain_with_document_prompt(self):
        mod = ChatOrchestratorModule()

        async def _run() -> None:
            with patch(
                "modules.chat_orchestrator.module.call_brain",
                new_callable=AsyncMock,
                return_value="В документе описаны котлы.",
            ) as m_brain:
                out = await mod.execute(
                    {
                        "input": {"payload": "", "type": "file"},
                        "context": {
                            "user_id": "1",
                            "file_context": {
                                "file_type": "document",
                                "local_path": "/tmp/manual.pdf",
                                "original_name": "manual.pdf",
                            },
                            "document_intake": {"ok": True, "text": "Газовые котлы ARDERIA..."},
                        },
                    }
                )
                m_brain.assert_awaited_once()
                ut = (m_brain.call_args.kwargs or {}).get("user_text") or (
                    m_brain.call_args.args[0] if m_brain.call_args.args else ""
                )
                self.assertIn("документ", ut.lower())
                self.assertIn("котлы", (out.payload or ""))

        asyncio.run(_run())

    def test_operational_diag_short_circuit_surfaces_in_output_meta(self):
        mod = ChatOrchestratorModule()

        async def fake_brain(user_text, context, system_prompt):
            context["operational_diag_short_circuit"] = True
            return "stub"

        async def _run() -> None:
            with patch("modules.chat_orchestrator.module.call_brain", side_effect=fake_brain):
                out = await mod.execute({"input": {"payload": "x"}, "context": {"user_id": "1"}})
            self.assertTrue(out.meta.get("operational_diag_short_circuit"))

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
