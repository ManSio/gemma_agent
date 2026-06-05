from core.brain.code_empty_recovery import (
    apply_code_delivery_if_needed,
    assistant_promised_code_without_body,
    content_is_placeholder,
    looks_like_code_payload,
    code_reply_incomplete,
    looks_like_internal_code_monologue,
    minimal_python_fallback,
    resolve_code_delivery_fallback,
    strip_cot_for_code,
    thread_awaits_code_body,
    user_requests_code,
)
from core.openrouter_completion_text import _placeholder_only_content


def test_placeholder_content():
    assert content_is_placeholder("----------")
    assert _placeholder_only_content("--- ---")
    assert not content_is_placeholder("print(1)")


def test_code_preserved_in_strip():
    raw = "```python\nprint('hi')\n```"
    assert "print" in strip_cot_for_code(raw)


def test_minimal_fallback():
    fb = minimal_python_fallback("напиши программу на python")
    assert "random" in fb
    assert "```python" in fb


def test_factorial_fallback():
    fb = minimal_python_fallback("напиши функцию на Python для факториала")
    assert "def factorial" in fb
    assert "```python" in fb


def test_internal_monologue_detected():
    leak = "Мы в режиме code_generation, пользователь просит функцию факториала. Нужно дать код."
    assert looks_like_internal_code_monologue(leak)
    assert not looks_like_internal_code_monologue(minimal_python_fallback("факториал python"))


def test_resolve_code_delivery_on_factorial():
    fb = resolve_code_delivery_fallback("напиши функцию на Python для факториала")
    assert "def factorial" in fb


def test_thread_awaits_code_after_colon_intro():
    ctx = {
        "recent_messages": [
            {"role": "user", "text": "напиши функцию на Python для факториала"},
            {
                "role": "assistant",
                "text": "Вот функция факториала на Python — рекурсивный вариант:",
            },
        ]
    }
    assert thread_awaits_code_body("Продолжай", ctx)
    assert assistant_promised_code_without_body(
        "Вот функция факториала на Python — рекурсивный вариант:"
    )


def test_user_requests_code():
    assert user_requests_code("напиши функцию на Python для факториала")
    assert not user_requests_code("привет")


def test_code_reply_incomplete_intro_only():
    assert code_reply_incomplete(
        "напиши функцию на Python для факториала",
        "Вот рекурсивная функция факториала на Python:",
    )
    assert code_reply_incomplete(
        "напиши функцию на Python для факториала",
        "Инструкция: Дай строго один ответ — текст или один TOOL_CALL.\n"
        "Не придумывай инструменты.",
    )
    assert not code_reply_incomplete(
        "напиши функцию на Python для факториала",
        minimal_python_fallback("факториал python"),
    )


def test_calculator_intro_gets_fallback():
    intro = "Вот улучшенная версия калькулятора на Python с поддержкой базовых операций:"
    out = apply_code_delivery_if_needed("Напиши калькулятор на python", intro)
    assert "```python" in out
    assert "def calculate" in out
    assert out != intro


def test_calculator_minimal_fallback():
    fb = minimal_python_fallback("напиши калькулятор на python")
    assert "def calculate" in fb
