from core import brain as brain_mod


def test_repetition_glitch_detected():
    bad = "We need to check the 100% of the 100% of the 100% of the 100% of the 100% " * 3
    assert brain_mod._looks_like_repetition_glitch(bad)
    assert not brain_mod._looks_like_repetition_glitch("Привет! Как дела?")


def test_natural_fallback_chitchat_on_llm_error():
    r = brain_mod._natural_fallback_response("llm_error", "u1", "привет")
    assert "Привет" in r or "Здравствуй" in r or "на связи" in r.lower()


def test_natural_fallback_generic_without_chitchat():
    r = brain_mod._natural_fallback_response("llm_error", "u1", "объясни квантовую физику подробно")
    low = r.lower()
    assert "ответ" in low
    assert any(
        phrase in low
        for phrase in ("с первого раза", "не сложился", "повтори запрос", "ещё раз", "напиши снова")
    )
