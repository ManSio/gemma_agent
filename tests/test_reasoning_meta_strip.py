from core.brain.reasoning_meta_strip import strip_reasoning_meta_leak


def test_strips_meta_preamble() -> None:
    raw = (
        "Мы должны дать итоговый ответ пользователю на задачу «Три мудреца».\n"
        "Колпак первого мудреца — белый, потому что третий видит два чёрных."
    )
    out = strip_reasoning_meta_leak(raw)
    assert "Мы должны" not in out
    assert "белый" in out


def test_keeps_clean_answer() -> None:
    raw = "Колпак первого — белый. Третий видит два чёрных, значит у него не чёрный."
    assert strip_reasoning_meta_leak(raw) == raw


def test_strips_hypercube_monologue() -> None:
    raw = (
        "Мы находимся в пятимерном кубе (пентеракте). Нужно ответить на три вопроса.\n"
        "1. 10\n2. 0\n3. 1"
    )
    out = strip_reasoning_meta_leak(raw)
    assert "Мы находимся" not in out
    assert "10" in out
