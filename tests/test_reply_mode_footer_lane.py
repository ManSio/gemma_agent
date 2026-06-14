"""Admin footer shows 3-lane tag."""
from __future__ import annotations

import unittest

from core.reply_mode_footer import build_mode_footer_fields
from core.turn_contract import LANE_FACT


class TestReplyModeFooterLane(unittest.TestCase):
    def test_lane_in_tag(self) -> None:
        fields = build_mode_footer_fields(
            output_meta={"turn_contract": {"lane": LANE_FACT}, "route_intent": "weather"},
            route_context={"turn_contract": {"lane": LANE_FACT}},
            trace_id="abc",
        )
        self.assertIn("факт", fields["human"])
        self.assertIn("L=FACT", fields["machine_tag"])


if __name__ == "__main__":
    unittest.main()
