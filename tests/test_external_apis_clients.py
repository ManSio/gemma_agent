import os
import unittest
from unittest.mock import patch

from modules.external_apis.clients import (
    CurrencyAPIClient,
    GenericSearchClient,
    NewsAPIClient,
    WeatherAPIClient,
    WikipediaClient,
    fetch_wttr_in_j1_summary,
    format_wttr_j1_summary_text,
)
from modules.external_apis.service import ExternalAPIService, _expand_search_query_variants


def _mock_hourly_48h(day1: str = "2024-06-01", day2: str = "2024-06-02") -> dict:
    times = [f"{day1}T{h:02d}:00" for h in range(24)] + [f"{day2}T{h:02d}:00" for h in range(24)]
    temps = [10.0 + (i % 7) * 0.5 for i in range(48)]
    codes = [3 if i % 2 == 0 else 61 for i in range(48)]
    return {"time": times, "temperature_2m": temps, "weather_code": codes}


class ExternalAPIsClientsTests(unittest.IsolatedAsyncioTestCase):
    async def test_weather_open_meteo(self):
        geo = {
            "results": [
                {
                    "name": "London",
                    "latitude": 51.5,
                    "longitude": -0.12,
                    "country": "United Kingdom",
                    "country_code": "GB",
                    "admin1": "England",
                }
            ]
        }
        fc = {
            "timezone_abbreviation": "GMT",
            "hourly": _mock_hourly_48h(),
            "daily": {
                "time": ["2024-06-01", "2024-06-02", "2024-06-03"],
                "weather_code": [3, 61, 2],
                "temperature_2m_max": [14.0, 16.0, 13.0],
                "temperature_2m_min": [8.0, 10.0, 9.0],
            },
            "current": {
                "time": "2024-06-01T12:00",
                "temperature_2m": 10.0,
                "relative_humidity_2m": 70,
                "apparent_temperature": 8.0,
                "weather_code": 3,
                "wind_speed_10m": 5.0,
            },
        }

        with patch(
            "modules.external_apis.clients._http_get_json",
            side_effect=[(200, geo), (200, fc)],
        ):
            w = WeatherAPIClient()
            out = await w.get_current(city="London", country="GB")
        self.assertTrue(out.get("configured"))
        self.assertIn("10.0 °C", out.get("summary", ""))
        self.assertIn("overcast", out.get("summary", ""))
        self.assertIn("Weather —", out.get("summary", ""))
        self.assertIn("Local time:", out.get("summary", ""))
        self.assertIn("every 3 h", out.get("summary", ""))
        self.assertIn("15:00", out.get("summary", ""))
        self.assertIn("Tomorrow", out.get("summary", ""))
        self.assertIn("Multi-day", out.get("summary", ""))

    async def test_weather_requires_city(self):
        w = WeatherAPIClient()
        out = await w.get_current(city="  ", country="")
        self.assertFalse(out.get("configured"))

    async def test_forecast_query_omits_time_in_current_param(self):
        """Open-Meteo отклоняет current=time,… (HTTP 400) — time приходит в JSON автоматически."""
        captured: list = []

        async def _capture(url: str):
            captured.append(url)
            if "geocoding" in url or "search" in url:
                return (
                    200,
                    {
                        "results": [
                            {
                                "name": "Minsk",
                                "latitude": 53.9,
                                "longitude": 27.5,
                                "country": "Belarus",
                                "country_code": "BY",
                                "admin1": "Minsk City",
                            }
                        ]
                    },
                )
            return (
                200,
                {
                    "timezone_abbreviation": "GMT+3",
                    "hourly": _mock_hourly_48h(),
                    "daily": {
                        "time": ["2024-06-01"],
                        "weather_code": [3],
                        "temperature_2m_max": [14.0],
                        "temperature_2m_min": [8.0],
                    },
                    "current": {
                        "time": "2024-06-01T12:00",
                        "temperature_2m": 10.0,
                        "relative_humidity_2m": 70,
                        "apparent_temperature": 8.0,
                        "weather_code": 3,
                        "wind_speed_10m": 5.0,
                    },
                },
            )

        with patch("modules.external_apis.clients._http_get_json", side_effect=_capture):
            w = WeatherAPIClient()
            out = await w.get_current(city="Minsk", country="BY")
        self.assertTrue(out.get("configured"))
        forecast_urls = [u for u in captured if "forecast" in u]
        self.assertTrue(forecast_urls)
        self.assertNotIn("current=time", forecast_urls[0])
        self.assertIn("current=temperature_2m", forecast_urls[0])

    def test_format_wttr_j1_summary_tomorrow_ru(self):
        payload = {
            "nearest_area": [
                {
                    "areaName": [{"value": "Город"}],
                    "country": [{"value": "Russia"}],
                    "region": [{"value": "Reg"}],
                }
            ],
            "current_condition": [
                {
                    "temp_C": "5",
                    "FeelsLikeC": "3",
                    "weatherDesc": [{"value": "Clear"}],
                    "humidity": "50",
                    "windspeedKmph": "10",
                }
            ],
            "weather": [
                {"date": "2026-05-10", "maxtempC": "10", "mintempC": "2", "avgtempC": "6", "hourly": []},
                {
                    "date": "2026-05-11",
                    "maxtempC": "15",
                    "mintempC": "8",
                    "avgtempC": "11",
                    "hourly": [
                        {"time": "1200", "tempC": "14", "lang_ru": [{"value": "Облачно"}]},
                    ],
                },
            ],
        }
        s = format_wttr_j1_summary_text(payload, forecast_day_index=1, ru=True)
        self.assertIn("Завтра", s)
        self.assertIn("2026-05-11", s)
        self.assertIn("15", s)
        self.assertIn("Облачно", s)

    async def test_fetch_wttr_in_j1_summary_uses_http_mock(self):
        payload = {
            "nearest_area": [{"areaName": [{"value": "Z"}], "country": [{"value": "X"}], "region": [{"value": "Y"}]}],
            "weather": [{"date": "2026-01-01", "maxtempC": "1", "mintempC": "0", "avgtempC": "1", "hourly": []}],
        }
        with patch("modules.external_apis.clients._http_get_json", return_value=(200, payload)):
            out = await fetch_wttr_in_j1_summary("Zcity", "")
        self.assertIsNotNone(out)
        self.assertIn("wttr.in", out or "")

    async def test_weather_belarus_retry_geocoding(self):
        empty_geo = {"results": []}
        geo_by = {
            "results": [
                {
                    "name": "Mikhanavichy",
                    "latitude": 53.75,
                    "longitude": 27.65,
                    "country": "Belarus",
                    "country_code": "BY",
                    "admin1": "Minsk Region",
                }
            ]
        }
        fc = {
            "timezone_abbreviation": "MSK",
            "hourly": _mock_hourly_48h("2024-01-15", "2024-01-16"),
            "daily": {
                "time": ["2024-01-15", "2024-01-16", "2024-01-17"],
                "weather_code": [3, 71, 3],
                "temperature_2m_max": [1.0, 0.0, -1.0],
                "temperature_2m_min": [-5.0, -6.0, -7.0],
            },
            "current": {
                "time": "2024-01-15T09:30",
                "temperature_2m": 5.0,
                "relative_humidity_2m": 80,
                "apparent_temperature": 3.0,
                "weather_code": 3,
                "wind_speed_10m": 4.0,
            },
        }
        with patch(
            "modules.external_apis.clients._http_get_json",
            side_effect=[(200, empty_geo), (200, geo_by), (200, fc)],
        ):
            w = WeatherAPIClient()
            out = await w.get_current(city="Гомель", country="Беларусь")
        self.assertTrue(out.get("configured"))
        self.assertIn("5.0 °C", out.get("summary", ""))
        self.assertIn("Погода —", out.get("summary", ""))
        self.assertIn("пасмурно", out.get("summary", ""))
        self.assertIn("каждые 3 ч", out.get("summary", ""))
        self.assertIn("Завтра", out.get("summary", ""))
        self.assertIn("По дням", out.get("summary", ""))

    async def test_currency_frankfurter(self):
        data = {"amount": 1.0, "base": "USD", "date": "2024-06-01", "rates": {"EUR": 0.92}}
        with patch("modules.external_apis.clients._http_get_json", return_value=(200, data)):
            c = CurrencyAPIClient()
            out = await c.get_rate(base="USD", quote="EUR")
        self.assertTrue(out.get("configured"))
        self.assertEqual(out.get("rate"), 0.92)
        self.assertIn("Frankfurter", out.get("summary", ""))

    async def test_wikipedia_summary(self):
        body = {
            "query": {
                "pages": {
                    "123": {
                        "pageid": 123,
                        "title": "Python",
                        "extract": "Python is a language.",
                    }
                }
            }
        }
        with patch("modules.external_apis.clients._http_get_json", return_value=(200, body)):
            wiki = WikipediaClient()
            out = await wiki.summary("Python")
        self.assertTrue(out.get("configured"))
        self.assertIn("Python is a language", out.get("extract", ""))

    async def test_duckduckgo_search(self):
        body = {
            "Heading": "Test",
            "AbstractText": "Abstract line.",
            "RelatedTopics": [{"Text": "Related A"}],
        }
        with patch("modules.external_apis.clients._http_get_json", return_value=(200, body)):
            s = GenericSearchClient()
            out = await s.search("query test")
        self.assertTrue(out.get("configured"))
        self.assertIn("Abstract line", out.get("summary", ""))

    async def test_duckduckgo_json_accepts_http_202(self):
        body = {"Heading": "H", "AbstractText": "From 202.", "RelatedTopics": []}
        with patch("modules.external_apis.clients._http_get_json", return_value=(202, body)):
            s = GenericSearchClient()
            out = await s.search("q")
        self.assertTrue(out.get("configured"))
        self.assertIn("From 202", out.get("summary", ""))

    async def test_duckduckgo_html_bot_wall(self):
        empty_instant = {"Heading": "", "AbstractText": "", "Answer": "", "RelatedTopics": []}
        bot_html = (
            "<html><body><div class=\"anomaly-modal__title\">Unfortunately, bots use DuckDuckGo too.</div></body></html>"
        )
        with patch("modules.external_apis.clients._http_get_json", return_value=(200, empty_instant)):
            with patch("modules.external_apis.clients._http_get_html", return_value=(200, bot_html)):
                s = GenericSearchClient()
                out = await s.search("test query")
        self.assertFalse(out.get("configured"))
        self.assertEqual(out.get("error"), "duckduckgo_bot_challenge")
        self.assertIn("SearXNG", out.get("hint", ""))

    async def test_duckduckgo_html_fallback_when_instant_empty(self):
        empty_instant = {"Heading": "", "AbstractText": "", "Answer": "", "RelatedTopics": []}
        html = """<html><body>
<div class="result results_links result__body">
  <h2 class="result__title"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2F">Example Site</a></h2>
  <a class="result__snippet">First snippet line.</a>
</div></body></html>"""
        with patch("modules.external_apis.clients._http_get_json", return_value=(200, empty_instant)):
            with patch("modules.external_apis.clients._http_get_html", return_value=(200, html)):
                s = GenericSearchClient()
                out = await s.search("my query")
        self.assertTrue(out.get("configured"))
        self.assertEqual(out.get("source"), "duckduckgo_html")
        self.assertIn("Example Site", out.get("summary", ""))
        self.assertIn("example.com", out.get("summary", ""))

    async def test_duckduckgo_html_accepts_http_202(self):
        empty_instant = {"Heading": "", "AbstractText": "", "Answer": "", "RelatedTopics": []}
        html = """<html><body>
<div class="result results_links result__body">
  <h2 class="result__title"><a class="result__a" href="https://example.com/page">Ex</a></h2>
  <a class="result__snippet">Snip</a>
</div></body></html>"""
        with patch("modules.external_apis.clients._http_get_json", return_value=(202, empty_instant)):
            with patch("modules.external_apis.clients._http_get_html", return_value=(202, html)):
                s = GenericSearchClient()
                out = await s.search("my query")
        self.assertTrue(out.get("configured"))
        self.assertEqual(out.get("source"), "duckduckgo_html")

    def test_duckduckgo_decode_href(self):
        from modules.external_apis.clients import _duckduckgo_decode_href

        self.assertEqual(
            _duckduckgo_decode_href("//duckduckgo.com/l/?uddg=https%3A%2F%2Ffoo.bar%2Fx"),
            "https://foo.bar/x",
        )

    def test_news_world_locale_russian_for_cyrillic_query(self):
        loc = NewsAPIClient._locale_for_topic("какие новости в мире", "BY")
        self.assertEqual(loc, "hl=ru&gl=BY&ceid=BY:ru")
        self.assertTrue(NewsAPIClient.wants_world_news("новости мира сегодня"))

    def test_news_world_locale_english_when_forced(self):
        with patch.dict(os.environ, {"NEWS_WORLD_RSS_LANG": "en"}, clear=False):
            loc = NewsAPIClient._locale_for_topic("какие новости в мире", "BY")
        self.assertEqual(loc, "hl=en&gl=US&ceid=US:en")

    def test_news_google_locale_us_env_ignored_for_cyrillic_unless_force(self):
        with patch.dict(
            os.environ,
            {
                "NEWS_GOOGLE_RSS_LOCALE": "hl=en&gl=US&ceid=US:en",
                "NEWS_GOOGLE_RSS_LOCALE_FORCE": "false",
                "NEWS_WORLD_RSS_LANG": "ru",
            },
            clear=False,
        ):
            loc = NewsAPIClient._locale_for_topic("какие новости в мире", "BY")
        self.assertEqual(loc, "hl=ru&gl=BY&ceid=BY:ru")

    def test_news_local_locale_when_regional_topic(self):
        loc = NewsAPIClient._locale_for_topic("новости Беларуси", "BY")
        self.assertIn("gl=BY", loc)

    def test_news_rss_world_query_normalized(self):
        self.assertEqual(
            NewsAPIClient._rss_search_topic("что в мире", country="BY"),
            "международные новости",
        )
        self.assertEqual(
            NewsAPIClient._rss_search_topic("world news today", country="US"),
            "world news",
        )

    async def test_news_rss(self):
        xml = """<?xml version="1.0"?>
<rss><channel>
<title>Google News</title>
<item><title>Headline One</title><link>https://example.com/1</link><source url="https://reuters.com/article1">Reuters</source></item>
<item><title>Headline Two</title><link>https://example.com/2</link><source url="https://bbc.com/article2">BBC</source></item>
</channel></rss>"""
        with patch("modules.external_apis.clients._http_get_text", return_value=(200, xml)):
            n = NewsAPIClient()
            out = await n.headlines(topic="science")
        self.assertTrue(out.get("configured"))
        self.assertEqual(len(out.get("items") or []), 2)
        self.assertIn("Headline One", out.get("summary", ""))
        self.assertIn("source", out["items"][0])

    async def test_news_rss_filters_junk_sources(self):
        xml = """<?xml version="1.0"?>
<rss><channel>
<title>Google News</title>
<item><title>Good News</title><link>https://reuters.com/good</link><source url="https://reuters.com">Reuters</source></item>
<item><title>Junk News</title><link>https://fathomjournal.org/fake</link><source url="https://fathomjournal.org">Fathom</source></item>
<item><title>Also Good</title><link>https://bbc.com/also</link><source url="https://bbc.com">BBC</source></item>
</channel></rss>"""
        with patch("modules.external_apis.clients._http_get_text", return_value=(200, xml)):
            n = NewsAPIClient()
            out = await n.headlines(topic="science")
        self.assertTrue(out.get("configured"))
        self.assertEqual(len(out.get("items") or []), 2)
        self.assertEqual(out["items"][0]["title"], "Good News")
        self.assertEqual(out["items"][1]["title"], "Also Good")

    def test_expand_search_query_variants_ru_recipe(self):
        v = _expand_search_query_variants("Как приготовить демьянку из баклажанов?")
        self.assertGreaterEqual(len(v), 3)
        self.assertTrue(any("рецепт" in x for x in v))
        self.assertTrue(any("баклажан" in x for x in v))

    async def test_search_variants_tries_next_query(self):
        empty_instant = {"Heading": "", "AbstractText": "", "Answer": "", "RelatedTopics": []}
        good = {"Answer": "найдено", "AbstractText": ""}
        with patch(
            "modules.external_apis.clients._http_get_json",
            side_effect=[(200, empty_instant), (200, good)],
        ):
            with patch("modules.external_apis.clients._http_get_html", return_value=(200, "<html></html>")):
                s = GenericSearchClient()
                out = await s.search_variants(["aaa нет такого", "bbb рецепт"])
        self.assertTrue(out.get("configured"))
        self.assertIn("найдено", out.get("summary", ""))

    async def test_lookup_chain_wikipedia_then_search(self):
        wiki_body = {
            "query": {
                "pages": {
                    "-1": {"missing": ""},
                }
            }
        }
        ddg = {"Answer": "42", "AbstractText": ""}
        with patch(
            "modules.external_apis.clients._http_get_json",
            side_effect=[(200, wiki_body), (200, ddg)],
        ):
            with patch(
                "modules.external_apis.clients._http_get_html",
                return_value=(200, "<html></html>"),
            ):
                svc = ExternalAPIService()
                out = await svc.lookup_or_fallback("life universe")
        self.assertEqual(out.get("source"), "search")
        self.assertTrue((out.get("data") or {}).get("configured"))


if __name__ == "__main__":
    unittest.main()
