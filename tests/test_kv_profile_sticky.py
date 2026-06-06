import unittest
from unittest.mock import patch

from core.brain.session_stickiness import _profile_for_kv_session


class KvProfileStickyTests(unittest.TestCase):
    def test_keeps_prev_profile_on_router_flip(self):
        st = {"kv_profile": "standard", "profile": "standard"}
        with patch.dict("os.environ", {"BRAIN_KV_PROFILE_STICKY": "true"}):
            out = _profile_for_kv_session(st, "short", reset_reason="", explicit_switch=False)
        self.assertEqual(out, "standard")

    def test_resets_profile_on_explicit_switch(self):
        st = {"kv_profile": "standard"}
        with patch.dict("os.environ", {"BRAIN_KV_PROFILE_STICKY": "true"}):
            out = _profile_for_kv_session(st, "short", reset_reason="", explicit_switch=True)
        self.assertEqual(out, "short")

    def test_resets_profile_on_epoch_reset(self):
        st = {"kv_profile": "standard"}
        with patch.dict("os.environ", {"BRAIN_KV_PROFILE_STICKY": "true"}):
            out = _profile_for_kv_session(st, "short", reset_reason="ttl_expired", explicit_switch=False)
        self.assertEqual(out, "short")

    def test_switches_family_code_to_summarize(self):
        st = {"kv_profile": "code_generation", "profile": "code_generation"}
        with patch.dict("os.environ", {"BRAIN_KV_PROFILE_STICKY": "true"}):
            out = _profile_for_kv_session(
                st, "summarization", reset_reason="", explicit_switch=False
            )
        self.assertEqual(out, "summarization")

    def test_keeps_family_within_chat(self):
        st = {"kv_profile": "standard", "profile": "standard"}
        with patch.dict("os.environ", {"BRAIN_KV_PROFILE_STICKY": "true"}):
            out = _profile_for_kv_session(st, "quick_explain", reset_reason="", explicit_switch=False)
        self.assertEqual(out, "standard")


if __name__ == "__main__":
    unittest.main()
