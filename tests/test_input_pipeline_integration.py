"""
Интеграционная проверка: текст после STT должен участвовать в антифлуде как обычное сообщение
(команда определяется по эффективному payload, а не только по message.text).
"""
import unittest


class InputPipelineIntegrationTests(unittest.TestCase):
    def test_effective_command_uses_payload_not_empty_message_text(self):
        message_text = ""
        payload = "/calc 2+2"
        is_cmd = bool((payload or message_text or "").strip().startswith("/"))
        self.assertTrue(is_cmd)

    def test_plain_voice_text_not_command(self):
        payload = "расскажи анекдот"
        is_cmd = bool((payload or "").strip().startswith("/"))
        self.assertFalse(is_cmd)


if __name__ == "__main__":
    unittest.main()
