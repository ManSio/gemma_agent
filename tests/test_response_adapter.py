import unittest

from core.models import Output
from core.response_adapter import UnifiedResponseAdapter


class ResponseAdapterTests(unittest.TestCase):
    def test_image_attachment_extraction(self):
        adapter = UnifiedResponseAdapter()
        out = Output(
            type="text",
            payload="done",
            meta={"module": "chat-orchestrator", "image_output_path": "/tmp/x.png"},
        )
        env = adapter.from_output(out)
        self.assertEqual(env.kind, "text")
        self.assertTrue(env.attachments)
        self.assertEqual(env.attachments[0]["type"], "image")

    def test_location_attachment(self):
        adapter = UnifiedResponseAdapter()
        out = Output(
            type="text",
            payload="here",
            meta={
                "module": "chat-orchestrator",
                "telegram_location_reply": {"latitude": 53.9, "longitude": 27.56},
            },
        )
        env = adapter.from_output(out)
        locs = [a for a in env.attachments if a.get("type") == "location"]
        self.assertEqual(len(locs), 1)
        self.assertEqual(locs[0]["latitude"], 53.9)


if __name__ == "__main__":
    unittest.main()
