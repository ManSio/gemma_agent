import json
import logging
import os
import unittest
from io import StringIO
from unittest.mock import patch

from core.logging_setup import JsonFormatter


class LoggingSetupTests(unittest.TestCase):
    def test_json_formatter_includes_gemma_event(self):
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="x",
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        record.gemma_event = "boot"
        record.plugin = "echo"
        line = fmt.format(record)
        d = json.loads(line)
        self.assertEqual(d["gemma_event"], "boot")
        self.assertEqual(d["plugin"], "echo")
        self.assertEqual(d["msg"], "hello")

    def test_setup_json_to_stream(self):
        buf = StringIO()
        with patch.dict(os.environ, {"LOG_FORMAT": "json", "LOG_LEVEL": "INFO"}, clear=False):
            root = logging.getLogger()
            root.handlers.clear()
            h = logging.StreamHandler(buf)
            h.setFormatter(JsonFormatter())
            root.addHandler(h)
            root.setLevel(logging.INFO)
            logging.getLogger("t").info("x", extra={"gemma_event": "e"})
            root.handlers.clear()
        line = buf.getvalue().strip()
        self.assertIn("gemma_event", line)

    def test_extra_module_name_conflicts_with_logrecord(self):
        """LogRecord reserves 'module' (Python source module); use gemma_module in extra."""
        log = logging.getLogger("t_reserved_extra")
        log.handlers.clear()
        log.addHandler(logging.NullHandler())
        log.setLevel(logging.INFO)
        log.propagate = False
        log.info("ok", extra={"gemma_module": "chat-orchestrator"})
        with self.assertRaises(KeyError):
            log.info("bad", extra={"module": "x"})


if __name__ == "__main__":
    unittest.main()
