import unittest
from unittest.mock import AsyncMock, patch

from core.geo_maps_client import haversine_km, point_in_polygon
from core.geo_reply_tokens import expand_telegram_geo_placeholders
from core.geo_zones_store import zone_add_circle, zones_check, zones_list


class GeoMathTests(unittest.TestCase):
    def test_haversine_minsk_approx(self):
        # Минск центр ~ 53.9, 27.56; Вильнюс ~ 54.68, 25.28 → ~170–180 км
        km = haversine_km(53.9, 27.56, 54.68, 25.28)
        self.assertGreater(km, 150)
        self.assertLess(km, 220)

    def test_point_in_square(self):
        ring = [[27.0, 53.0], [28.0, 53.0], [28.0, 54.0], [27.0, 54.0]]
        self.assertTrue(point_in_polygon(27.5, 53.5, ring))
        self.assertFalse(point_in_polygon(26.5, 53.5, ring))


class GeoZonesTests(unittest.TestCase):
    def test_circle_zone(self):
        with patch("core.geo_zones_store._path") as mp:
            import tempfile
            from pathlib import Path

            td = Path(tempfile.mkdtemp())
            mp.return_value = td / "geo_zones.json"
            zone_add_circle("u1", "home", 53.9, 27.56, 2.0)
            self.assertEqual(len(zones_list("u1")), 1)
            hit = zones_check("u1", 53.91, 27.57)
            self.assertIn("home", hit.get("inside", []))


class GeoReplyTokensTests(unittest.IsolatedAsyncioTestCase):
    async def test_loc_strip(self):
        raw = "Вот точка.\n[[loc:53.9,27.56]]"
        out, meta = await expand_telegram_geo_placeholders(raw)
        self.assertNotIn("[[loc:", out)
        self.assertEqual(meta["telegram_location_reply"]["latitude"], 53.9)
        self.assertEqual(meta["telegram_location_reply"]["longitude"], 27.56)

    async def test_map_invokes_fetch(self):
        raw = "Карта:\n[[map:53.9,27.56,12]]"
        with patch("core.geo_maps_client.fetch_static_map_to_file", new_callable=AsyncMock) as fm:
            fm.return_value = "/tmp/fake.png"
            out, meta = await expand_telegram_geo_placeholders(raw)
            self.assertNotIn("[[map:", out)
            self.assertEqual(meta.get("image_output_path"), "/tmp/fake.png")
            fm.assert_awaited()


if __name__ == "__main__":
    unittest.main()
