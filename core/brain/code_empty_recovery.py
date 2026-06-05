"""
Восстановление пустого ответа на запросы кода (DeepSeek reasoning / cot_strip).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from core.brain.cot_strip import strip_leaked_cot
from core.brain.text_helpers import safe_text
from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"^[-_=.\s│─—]+$")
_CODE_MARKERS_RE = re.compile(
    r"(?m)(^```|```$|^def\s+\w+|^class\s+\w+|^import\s+\w+|^from\s+\w+\s+import)"
)
_CODE_REQUEST_RE = re.compile(
    r"(?i)(факториал|factorial|напиши\s+код|функци\w*\s+на\s+python|"
    r"python\s+для|программ\w*\s+на\s+python|def\s+\w+.*python|код\s+на\s+python|"
    r"калькулятор|calculator|напиши\s+.*python|программ\w*\s+на\s+питон)"
)
_INTERNAL_CODE_MONOLOGUE_MARKERS = (
    "режиме code_generation",
    "code_generation",
    "пользователь просит",
    "нужно дать",
    "нужно написать",
    "мы в режиме",
    "никаких tool_call",
    "краткое объяснение",
)
_CONTINUATION_AFTER_CODE_RE = re.compile(
    r"(?i)^\s*(да|ок|ага|угу|ладно|yes|ok|yep|yeah|продолж\w*|дальше|ещ[её]|continue|go\s+on)\s*[\.\!\?…]*\s*$"
)


def content_is_placeholder(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if _PLACEHOLDER_RE.fullmatch(t):
        return True
    if len(t) < 80 and t.count("-") > len(t) * 0.5:
        return True
    return False


def looks_like_code_payload(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _CODE_MARKERS_RE.search(t):
        return True
    if "print(" in t or "input(" in t:
        return True
    return False


def user_requests_code(user_text: str) -> bool:
    return bool(_CODE_REQUEST_RE.search(user_text or ""))


def looks_like_internal_code_monologue(text: str) -> bool:
    """CoT/reasoning про «режим code_generation» без самого кода."""
    t = (text or "").strip()
    if not t or looks_like_code_payload(t):
        return False
    low = t.lower()
    hits = sum(1 for m in _INTERNAL_CODE_MONOLOGUE_MARKERS if m in low)
    if hits >= 2:
        return True
    if hits >= 1 and len(t) < 420:
        return True
    return False


def assistant_promised_code_without_body(assistant_text: str) -> bool:
    t = (assistant_text or "").strip()
    if not t or looks_like_code_payload(t):
        return False
    low = t.lower()
    if not any(
        w in low
        for w in (
            "факториал",
            "factorial",
            "функци",
            "python",
            "питон",
            "рекурсив",
            "итератив",
            "калькулятор",
            "calculator",
            "программ",
        )
    ):
        return False
    if len(t) <= 280:
        return True
    return (
        t.rstrip().endswith(":")
        or "вариант" in low
        or "вот функцию" in low
        or "улучшенн" in low
        or "базовых операций" in low
    )


def code_reply_incomplete(user_text: str, assistant_text: str) -> bool:
    """Запрос кода, а в ответе нет fenced/def — только вводная фраза."""
    if not user_requests_code(user_text):
        return False
    t = (assistant_text or "").strip()
    if not t or looks_like_code_payload(t):
        return False
    try:
        from core.brain.response_finalize import looks_like_prompt_instruction_leak

        if looks_like_prompt_instruction_leak(t):
            return True
    except Exception as e:
        logger.debug("code_reply_incomplete instruction leak check: %s", e)
    if looks_like_internal_code_monologue(t):
        return True
    return assistant_promised_code_without_body(t)


def _dialogue_rows(rows: Optional[Sequence[Any]]) -> List[dict]:
    out: List[dict] = []
    if not rows:
        return out
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
    return out


def last_assistant_from_context(context: Optional[Dict[str, Any]]) -> str:
    ctx = context if isinstance(context, dict) else {}
    rd = ctx.get("recent_dialogue") or ctx.get("recent_messages") or []
    for row in reversed(_dialogue_rows(rd)):
        if str(row.get("role") or "").strip().lower() in ("assistant", "bot"):
            return str(row.get("text") or row.get("content") or "").strip()
    ds = ctx.get("dialogue_state")
    if isinstance(ds, dict):
        return str(ds.get("last_assistant_excerpt") or "").strip()
    return ""


def thread_awaits_code_body(
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> bool:
    """Короткое «да»/«продолжай» после обещания кода без блока ```."""
    ut = (user_text or "").strip()
    if not ut or not _CONTINUATION_AFTER_CODE_RE.match(ut):
        return False
    ctx = context if isinstance(context, dict) else {}
    last = last_assistant_from_context(ctx)
    if assistant_promised_code_without_body(last):
        return True
    rd = ctx.get("recent_dialogue") or ctx.get("recent_messages") or []
    for row in reversed(_dialogue_rows(rd)):
        if str(row.get("role") or "").strip().lower() != "user":
            continue
        prev_user = str(row.get("text") or row.get("content") or "")
        if user_requests_code(prev_user) and assistant_promised_code_without_body(last):
            return True
        break
    return False


def resolve_code_delivery_fallback(user_text: str) -> str:
    """Детерминированный код, если фильтры съели ответ LLM."""
    if not user_requests_code(user_text):
        return ""
    return (minimal_python_fallback(user_text) or "").strip()


def apply_code_delivery_if_needed(user_text: str, reply: str) -> str:
    """Подставить детерминированный код, если в ответе только вводная без ```/def."""
    t = (reply or "").strip()
    if not t or not user_requests_code(user_text):
        return t
    if not code_reply_incomplete(user_text, t):
        return t
    fb = resolve_code_delivery_fallback(user_text)
    return fb if fb else t


def strip_cot_for_code(text: str) -> str:
    """CoT-снятие без уничтожения блока кода."""
    t = (text or "").strip()
    if not t:
        return ""
    if looks_like_internal_code_monologue(t):
        return ""
    if looks_like_code_payload(t):
        return strip_leaked_cot(t) if len(t) < 320 else t
    return strip_leaked_cot(t)


_CODE_RETRY_SYSTEM = (
    "Ты пишешь код на Python по запросу пользователя. "
    "Сначала рабочий код (можно в ```python … ```), затем 1–2 предложения на русском как запустить. "
    "Без рассуждений, без TOOL_CALL, без markdown-заголовков ##."
)


def minimal_python_fallback(user_text: str) -> str:
    """Детерминированный fallback, если LLM дважды вернул пустоту."""
    low = (user_text or "").lower()
    if not re.search(r"(?i)(python|питон|py\b|программ|функци)", low):
        return ""
    if re.search(r"(?i)калькулятор|calculator", low):
        return (
            "```python\n"
            "def calculate(a: float, op: str, b: float) -> float:\n"
            "    if op == '+':\n"
            "        return a + b\n"
            "    if op == '-':\n"
            "        return a - b\n"
            "    if op == '*':\n"
            "        return a * b\n"
            "    if op == '/':\n"
            "        if b == 0:\n"
            "            raise ZeroDivisionError('деление на ноль')\n"
            "        return a / b\n"
            "    raise ValueError(f'неизвестная операция: {op}')\n\n"
            "def main() -> None:\n"
            "    print('Калькулятор (+ - * /). Пустая строка — выход.')\n"
            "    while True:\n"
            "        raw = input('a op b (например 2 + 3): ').strip()\n"
            "        if not raw:\n"
            "            break\n"
            "        parts = raw.split()\n"
            "        if len(parts) != 3:\n"
            "            print('Формат: число операция число')\n"
            "            continue\n"
            "        try:\n"
            "            a, op, b = float(parts[0]), parts[1], float(parts[2])\n"
            "            print('=', calculate(a, op, b))\n"
            "        except Exception as e:\n"
            "            print('Ошибка:', e)\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
            "```\n\n"
            "Сохрани в calc.py и запусти: python calc.py"
        )
    if re.search(r"(?i)факториал|factorial", low):
        return (
            "```python\n"
            "def factorial(n: int) -> int:\n"
            "    if n < 0:\n"
            "        raise ValueError('n must be non-negative')\n"
            "    out = 1\n"
            "    for i in range(2, n + 1):\n"
            "        out *= i\n"
            "    return out\n\n"
            "if __name__ == '__main__':\n"
            "    print(factorial(5))  # 120\n"
            "```\n\n"
            "Рекурсивный вариант: `return 1 if n < 2 else n * factorial(n - 1)`."
        )
    return (
        "```python\n"
        "import random\n\n"
        "def main():\n"
        "    secret = random.randint(1, 10)\n"
        "    print('Угадай число от 1 до 10')\n"
        "    while True:\n"
        "        raw = input('Твой вариант: ').strip()\n"
        "        if not raw.isdigit():\n"
        "            print('Введи число')\n"
        "            continue\n"
        "        n = int(raw)\n"
        "        if n < secret:\n"
        "            print('Больше')\n"
        "        elif n > secret:\n"
        "            print('Меньше')\n"
        "        else:\n"
        "            print('Верно!')\n"
        "            break\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
        "```\n\n"
        "Сохрани в файл guess.py и запусти: python guess.py"
    )


async def try_recover_empty_code_reply(
    *,
    llm: Any,
    user_text: str,
    brain_profile: str,
    first_result: Dict[str, Any],
    task_tier: str,
    telemetry_extra: Optional[Dict[str, Any]] = None,
    llm_session_id: str = "",
) -> str:
    if brain_profile not in ("code_generation", "code_debug"):
        return ""
    if not effective_bool("BRAIN_CODE_EMPTY_RETRY", default=True):
        return ""
    try:
        from core.brain.response_finalize import looks_like_prompt_instruction_leak
    except Exception:
        looks_like_prompt_instruction_leak = lambda _t: False  # type: ignore
    raw0 = safe_text(first_result.get("content", ""))
    stripped0 = strip_cot_for_code(raw0)
    if (stripped0 or "").strip() and looks_like_code_payload(stripped0):
        return stripped0.strip()
    if (stripped0 or "").strip() and not looks_like_prompt_instruction_leak(stripped0):
        return stripped0.strip()

    usage = first_result.get("usage_detail") or {}
    comp = int(usage.get("completion_tokens") or 0)
    if comp > 0:
        logger.info(
            "[code_recovery] retry after empty strip profile=%s completion_tokens=%s",
            brain_profile,
            comp,
        )

    try:
        from core.llm_tiered import llm_generate_tiered
        from core.resilience import with_timeout

        tiered = effective_bool("BRAIN_LLM_TIERED_RETRY", default=True)
        if tiered:
            retry = await llm_generate_tiered(
                llm,
                tag="llm_code_empty_retry",
                prompt=(user_text or "").strip(),
                system_prompt=_CODE_RETRY_SYSTEM,
                max_tokens=1800,
                temperature=0.15,
                base_timeout=None,
                task_tier=task_tier,
                telemetry_tag="brain_code_retry",
                telemetry_extra=telemetry_extra,
                session_id=llm_session_id,
                conversation_id=llm_session_id,
            )
        else:
            retry = await with_timeout(
                llm.generate(
                    prompt=(user_text or "").strip(),
                    system_prompt=_CODE_RETRY_SYSTEM,
                    max_tokens=1800,
                    temperature=0.15,
                    telemetry_tag="brain_code_retry",
                    telemetry_extra=telemetry_extra,
                    session_id=llm_session_id,
                    conversation_id=llm_session_id,
                ),
                timeout_sec=90.0,
                tag="llm_code_empty_retry",
            )
    except Exception as e:
        logger.warning("[code_recovery] retry failed: %s", e)
        return minimal_python_fallback(user_text)

    if retry.get("error"):
        return minimal_python_fallback(user_text)

    raw = safe_text(retry.get("content", ""))
    out = strip_cot_for_code(raw)
    if (out or "").strip():
        return out.strip()
    return minimal_python_fallback(user_text)
