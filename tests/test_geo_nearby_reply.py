import unittest

from core.geo_nearby_reply import is_nearby_request, _parse_nearby_categories


class GeoNearbyReplyTests(unittest.TestCase):
    def test_nearby_detect(self):
        self.assertTrue(is_nearby_request("что рядом"))
        self.assertTrue(is_nearby_request("Что рядом?"))
        self.assertTrue(is_nearby_request("кафе рядом"))
        self.assertFalse(is_nearby_request("привет"))

    def test_relational_ryadom_not_geo(self):
        dental = (
            "ситуация такая один зуб гнилой нужно удалять. рядом с ним зуб с хроническим "
            "пульпитом пролечили и поставили цементную пломбу. они находятся рядом с друг другом. "
            "Какой план лечения этих зубов?"
        )
        self.assertFalse(is_nearby_request(dental))
        self.assertFalse(is_nearby_request("два здания стоят рядом с друг другом"))
        self.assertFalse(is_nearby_request("рядом с ним лежит книга"))

    def test_categories(self):
        self.assertIn("кафе", _parse_nearby_categories("кафе рядом"))
        self.assertEqual(len(_parse_nearby_categories("что рядом")), 3)


if __name__ == "__main__":
    unittest.main()
