"""Снятие chat-Markdown для plain Telegram."""

from core.brain.text_helpers import strip_chat_markdown_for_telegram


def test_strip_bold_and_list_like_ms_bot():
    s = (
        "**Кратко:** Трава краснеет.\n\n"
        "1. **Антоцианы** — пигмент\n"
        "2. **Виды** — оттенок"
    )
    out = strip_chat_markdown_for_telegram(s)
    assert "**" not in out
    assert "Кратко:" in out
    assert "Антоцианы" in out
    assert "Виды" in out


def test_preserves_footnote_star_after_word():
    s = "См. определение ООН* в тексте."
    assert strip_chat_markdown_for_telegram(s) == s


def test_strip_italic_single_asterisks():
    s = "Это *курсив* в тексте."
    out = strip_chat_markdown_for_telegram(s)
    assert "*" not in out
    assert "курсив" in out


def test_strip_double_underscore():
    s = "Поле __value__ здесь."
    out = strip_chat_markdown_for_telegram(s)
    assert "__" not in out
    assert "value" in out
