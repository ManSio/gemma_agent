from unittest.mock import MagicMock

from core.user_facts import UserFactsManager, format_fact_fields_nice_ru


def test_format_fact_fields_ru():
    assert "страну" in format_fact_fields_nice_ru({"country"})
    s = format_fact_fields_nice_ru({"country", "city"})
    assert "страну" in s and "населённый" in s


def test_extract_city_moy_gorod_phrase():
    m = UserFactsManager(behavior_store=MagicMock())
    out = m.extract_facts("Мой город Санкт-Петербург")
    cities = [x for x in out if x.get("field") == "city" and x.get("valid")]
    assert cities
    assert "Петербург" in cities[0].get("value", "")


def test_extract_city_gorod_colon():
    m = UserFactsManager(behavior_store=MagicMock())
    out = m.extract_facts("Город: Минск")
    cities = [x for x in out if x.get("field") == "city" and x.get("valid")]
    assert cities
    assert cities[0].get("value") == "Минск"


def test_extract_city_ag_mikhanovichi():
    m = UserFactsManager(behavior_store=MagicMock())
    for text in (
        "Я живу а.г. Springfield",
        "запомни а.г. Springfield",
        "запомни город а.г. Springfield",
    ):
        out = m.extract_facts(text)
        cities = [x for x in out if x.get("field") == "city" and x.get("valid")]
        assert cities, text
        val = str(cities[0].get("value") or "")
        assert "springfield" in val.lower()
        assert "example county" in val.lower() or "минский" in val.lower()


def test_polgoda_ag_mikhanovichi_auto_commit():
    store = MagicMock()
    rec = {
        "user_facts": {},
        "user_facts_meta": {},
        "pending_facts_confirmation": {},
        "pending_facts_overwrite": {},
    }
    store.load.return_value = dict(rec)

    def _save(_uid, _gid, payload):
        rec.update(payload)

    store.save.side_effect = _save
    m = UserFactsManager(behavior_store=store)
    r = m.process_turn("900000001", None, "Полгода в а.г. Springfield")
    assert r.get("confirmation_prompt") is None
    assert "springfield" in str(r.get("facts", {}).get("city") or "").lower()


def test_weather_query_does_not_reask_city_confirmation():
    store = MagicMock()
    rec = {
        "user_facts": {"city": "аг. Springfield, Example County", "country": "BY"},
        "user_facts_meta": {},
        "pending_facts_confirmation": {},
        "pending_facts_overwrite": {},
    }
    store.load.return_value = dict(rec)
    m = UserFactsManager(behavior_store=store)
    r = m.process_turn("900000001", None, "Погода в а.г.Springfield")
    assert r.get("confirmation_prompt") is None


def test_no_city_from_math_gorod_inequality_phrase():
    m = UserFactsManager(behavior_store=MagicMock())
    out = m.extract_facts("Тут про город неравенства вида, реши задачу")
    cities = [x for x in out if x.get("field") == "city" and x.get("valid")]
    assert not cities


def test_no_valid_city_moy_gorod_inequality():
    m = UserFactsManager(behavior_store=MagicMock())
    out = m.extract_facts("мой город неравенства вида")
    cities = [x for x in out if x.get("field") == "city" and x.get("valid")]
    assert not cities


def test_no_city_from_portfolio_iz_aktsiy_phrase():
    m = UserFactsManager(behavior_store=MagicMock())
    text = "смоделируй диверсификацию портфеля из акций и облигаций"
    out = m.extract_facts(text)
    cities = [x for x in out if x.get("field") == "city"]
    assert not cities


def test_process_turn_skips_confirmation_on_portfolio_question():
    store = MagicMock()
    rec = {
        "user_facts": {"name": "Алексей", "city": "Гомель", "country": "Беларусь"},
        "user_facts_meta": {},
        "pending_facts_confirmation": {},
        "pending_facts_overwrite": {},
    }
    store.load.return_value = dict(rec)
    m = UserFactsManager(behavior_store=store)
    r = m.process_turn(
        "900000001",
        None,
        "смоделируй диверсификацию портфеля из акций и облигаций",
    )
    assert r.get("confirmation_prompt") is None
    assert not r.get("accepted_candidates")


def test_extract_belarus_agro_intro():
    m = UserFactsManager(behavior_store=MagicMock())
    text = (
        "Я живу в Республике Беларусь, аг. Дружный, зовут Алексей ,  "
        "я 1995 года рождения, Валюта BYN"
    )
    out = m.extract_facts(text)
    countries = [x for x in out if x.get("field") == "country" and x.get("valid")]
    cities = [x for x in out if x.get("field") == "city" and x.get("valid")]
    assert countries and countries[0].get("value") == "Беларусь"
    assert cities and "Дружный" in (cities[0].get("value") or "")


def test_commit_sanitizes_junk_city():
    store = MagicMock()
    rec: dict = {"user_facts": {}, "user_facts_meta": {}}

    def _load(uid, gid):
        return dict(rec)

    def _save(uid, gid, r):
        rec.clear()
        rec.update(r)

    store.load.side_effect = _load
    store.save.side_effect = _save
    m = UserFactsManager(behavior_store=store)
    m.commit_validated(
        "1",
        None,
        {
            "city": {
                "field": "city",
                "value": "неравенства вида",
                "confidence": 0.95,
                "valid": True,
                "source": "test",
            }
        },
    )
    assert "city" not in (rec.get("user_facts") or {})


def test_required_missing_currency_meta_feedback_not_nagged():
    """Разбор ответа со словами «валюта/структура» и процентами — не просим профильную валюту."""
    m = UserFactsManager(behavior_store=MagicMock())
    text = (
        "Нужна структура v2.0: оценка ~70%. Уточни про валюту в ответе, без дискурса про банки."
    )
    missing = m.required_missing_for_task(text, {})
    assert "currency" not in missing


def test_required_missing_currency_bank_scenario_with_eur():
    m = UserFactsManager(behavior_store=MagicMock())
    text = (
        "Банк ограничил валютные переводы. Нужно 800–1000 EUR наличными, депозит отеля 200 EUR."
    )
    missing = m.required_missing_for_task(text, {})
    assert "currency" not in missing


def test_required_missing_currency_rate_question_without_profile():
    m = UserFactsManager(behavior_store=MagicMock())
    missing = m.required_missing_for_task("Какой сегодня курс доллара?", {})
    assert "currency" in missing


def test_required_missing_currency_inflected_kurs_with_eur():
    m = UserFactsManager(behavior_store=MagicMock())
    missing = m.required_missing_for_task("Обменник хуже курса на 1,5 %, нужно 1000 EUR.", {})
    assert "currency" not in missing


def test_required_missing_currency_explicit_iso_skips_profile_nag():
    """Явные ISO в запросе — не цепляем auto_ask про профиль (сценарий самодостаточен)."""
    m = UserFactsManager(behavior_store=MagicMock())
    missing = m.required_missing_for_task("Сколько 100 USD в белорусских?", {})
    assert "currency" not in missing
