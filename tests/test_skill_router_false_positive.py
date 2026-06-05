from modules.skills.router import detect_skill_intent


def test_reakciya_not_shopping():
    assert detect_skill_intent("химическая реакция в пробирке") != "shopping_deals"


def test_stock_context_crypto():
    assert detect_skill_intent("купить акции на бирже") == "crypto_invest"
