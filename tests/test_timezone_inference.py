from core.timezone_inference import format_clock_hint_for_llm, infer_timezone_from_facts


def test_infer_belarus_city():
    assert infer_timezone_from_facts({"city": "Гомель", "country": "Беларусь"}) == "Europe/Minsk"


def test_infer_explicit_timezone_wins():
    assert infer_timezone_from_facts({"timezone": "Europe/Berlin", "country": "BY"}) == "Europe/Berlin"


def test_format_clock_hint_has_utc():
    h = format_clock_hint_for_llm(effective_tz="Europe/Minsk", telegram_message_unix=1_700_000_000)
    assert "UTC" in h
    assert "Europe/Minsk" in h
    assert "Календарь" in h
    assert "сегодня=" in h and "вчера=" in h
    assert "recent_dialogue" in h
    assert "не утверждай" in h.lower() or "не утверждай" in h


def test_format_clock_hint_unknown_tz_uses_utc_calendar():
    h = format_clock_hint_for_llm(effective_tz=None, telegram_message_unix=None)
    assert "Календарь (UTC)" in h
    assert "сегодня=" in h and "вчера=" in h and "завтра=" in h
    assert "погод" in h.lower()
