"""
Контракты продукта: pivot темы, обязательный search для цен/товаров, ворота ответа.

См. docs/PRODUCT_BEHAVIOR_CONTRACT_RU.md
"""
from __future__ import annotations

import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CYR_PRICE_WORD = r"(?<![а-яёa-z])(?:цена|цен[аы]|стоимость)(?![а-яёa-z])"
_COMMERCE_RE = re.compile(
    r"(?i)(samsung|galaxy|iphone|xiaomi|redmi|pixel|"
    + _CYR_PRICE_WORD + r"|"
    r"магазин|купить|"
    r"mediamarkt|смартфон|телефон\s+\w|найди\s+мне)",
)
_SCIENCE_RE = re.compile(
    r"(?i)(земл[яи]|неб[оа]|огон[ьи]|горит|кругл[аяой]|"
    r"космос|вселенн|гравитац|физик|хими[яи]|биолог|"
    r"почему\s+(небо|земля|огонь|вода|космос))",
)
_COSMOLOGY_RE = re.compile(
    r"(?i)(космос|вселенн|черн(?:ый|ое)\s+неб|звезд|галактик|"
    r"большой\s+взрыв|орбит|вакуум)",
)
_BOT_SYSTEM_LEAK_RE = re.compile(
    r"(?i)(нашей\s+систем|моделир.*огранич|тестировать\s+е[ёе]\s+работ|"
    r"вычислительн.*цикл|лимит.*запрос|telegram|gemma|openrouter|"
    r"пока\s+не\s+пройден\s+набор\s+проверок)",
)
_SCOPE_CLARIFY_RE = re.compile(
    r"(?i)(?:^|\s)(?:я\s+)?(?:про|о|об|речь\s+о)\s+",
)
_PRICE_SEARCH_RE = re.compile(
    r"(?i)(" + _CYR_PRICE_WORD + r"|"
    r"сколько\s+стоит|"
    r"магазин|купить|"
    r"посмотри\s+цен|"
    r"в\s+рб|беларус)",
)
_PRODUCT_SEARCH_RE = re.compile(
    r"(?i)(найди|найти|поиск|всё\s+про|все\s+про|"
    r"информаци[яю].*про|данные\s+про)",
)
_CORRECTION_SEARCH_RE = re.compile(
    r"(?i)(мог\s+(бы\s+)?(взять|искать|найти)|"
    r"почему\s+не\s+(искал|нашёл|нашел)|"
    r"цены?\s+и\s+магазин|не\s+то\s+что\s+искал)",
)
# «как найти друзей» / знакомства — не commerce search contract
_SOCIAL_ADVICE_RE = re.compile(
    r"(?i)(друз|знакомств|девушк|парн|отношен|одиноч|любов|семь|свидан|"
    r"первый\s+луч|рассвет|восход)",
)
_COMMERCE_REPLY_RE = re.compile(
    r"(?i)(samsung|galaxy\s*s\d|мтс|mobistore|"
    r"mediamarkt|onliner|₽|руб\.?/мес|byn)",
)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def product_behavior_enabled() -> bool:
    return _truthy("PRODUCT_BEHAVIOR_ENABLED", True)


def pivot_gate_enabled() -> bool:
    return product_behavior_enabled() and _truthy("PRODUCT_BEHAVIOR_PIVOT_GATE", True)


def search_contract_enabled() -> bool:
    return product_behavior_enabled() and _truthy("PRODUCT_BEHAVIOR_SEARCH_CONTRACT", True)


def reply_gate_enabled() -> bool:
    return product_behavior_enabled() and _truthy("PRODUCT_BEHAVIOR_REPLY_GATE", True)


def pivot_reset_dialog_enabled() -> bool:
    """Сброс KV/dialog_state при pivot — по умолчанию выкл. (режет нить диалога)."""
    return product_behavior_enabled() and _truthy("PRODUCT_BEHAVIOR_PIVOT_RESET_DIALOG", False)


def pivot_epoch_bump_enabled() -> bool:
    """Новый conversation_epoch при смене темы — лучше KV-reuse внутри темы."""
    return product_behavior_enabled() and _truthy("PRODUCT_BEHAVIOR_PIVOT_EPOCH_BUMP", True)


def _pivot_epoch_bump_enabled() -> bool:
    return pivot_epoch_bump_enabled()


def pivot_recent_keep_count() -> int:
    try:
        v = int((os.getenv("PRODUCT_BEHAVIOR_PIVOT_RECENT_KEEP") or "6").strip())
    except ValueError:
        v = 6
    return max(4, min(12, v))


def subject_bucket(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "general"
    if (_SCIENCE_RE.search(t) or _COSMOLOGY_RE.search(t)) and not _COMMERCE_RE.search(t):
        return "science"
    if _COMMERCE_RE.search(t):
        return "commerce"
    return "general"


def cosmology_in_text(text: str) -> bool:
    return bool(_COSMOLOGY_RE.search(text or ""))


def cosmology_thread_active(recent_dialogue: Any) -> bool:
    if not isinstance(recent_dialogue, list):
        return False
    for row in recent_dialogue[-8:]:
        if not isinstance(row, dict):
            continue
        if str(row.get("role") or "").lower() not in ("user", ""):
            continue
        if cosmology_in_text(str(row.get("text") or row.get("content") or "")):
            return True
    return False


def build_cosmology_scope_hint(user_text: str, recent_dialogue: Any) -> str:
    """
    «Система/тест/циклы» в нить про космос — не про Gemma.
    «я про вселенную» — явное уточнение предмета.
    """
    if not product_behavior_enabled():
        return ""
    ut = (user_text or "").strip()
    active = cosmology_thread_active(recent_dialogue) or cosmology_in_text(ut)
    if not active:
        return ""
    clarify = bool(_SCOPE_CLARIFY_RE.search(ut)) and cosmology_in_text(ut)
    ambiguous_ops = bool(
        re.search(r"(?i)(систем|ограничен|циклов|миллиард|тест\b)", ut)
    )
    if clarify or ambiguous_ops or cosmology_in_text(ut):
        return (
            "DOMAIN_SCOPE: речь о физической Вселенной и космологии (наука). "
            "Не переводи «система», «ограничения», «тест», «циклы» как про Telegram-бота, "
            "Gemma, API, лимиты запросов или внутренние проверки ПО."
        )
    return ""


def topic_pivot(user_text: str, prior_topic: str) -> bool:
    """Смена предмета (commerce ↔ science), не только явная фраза «другая тема»."""
    prior = (prior_topic or "").strip()
    if len(prior) < 8:
        return False
    old_b = subject_bucket(prior)
    new_b = subject_bucket(user_text)
    if old_b == "general" or new_b == "general":
        return False
    return old_b != new_b


def is_social_advice_not_commerce(user_text: str) -> bool:
    """Жизненные/социальные вопросы с «найти» — не требуют UniversalSearch."""
    t = (user_text or "").strip()
    if not t:
        return False
    if _SOCIAL_ADVICE_RE.search(t) and not _COMMERCE_RE.search(t):
        return True
    return False


def should_force_product_search(user_text: str) -> bool:
    if not search_contract_enabled():
        return False
    t = (user_text or "").strip()
    if not t:
        return False
    if is_social_advice_not_commerce(t):
        return False
    if _CORRECTION_SEARCH_RE.search(t):
        return True
    if _PRICE_SEARCH_RE.search(t):
        return True
    if _PRODUCT_SEARCH_RE.search(t) and _COMMERCE_RE.search(t):
        return True
    if _PRODUCT_SEARCH_RE.search(t) and len(t) > 12 and _PRICE_SEARCH_RE.search(t):
        return True
    return False


def price_or_commerce_search_required(user_text: str) -> bool:
    """Строже should_force_product_search: только цена/товар, не «как/про» в науке."""
    if not search_contract_enabled():
        return False
    t = (user_text or "").strip()
    if not t:
        return False
    if is_social_advice_not_commerce(t):
        return False
    if _PRICE_SEARCH_RE.search(t):
        return True
    return bool(_PRODUCT_SEARCH_RE.search(t) and _COMMERCE_RE.search(t))


def enrich_search_query(query: str, user_facts: Optional[Dict[str, Any]]) -> str:
    q = (query or "").strip()
    if not q:
        return q
    facts = user_facts if isinstance(user_facts, dict) else {}
    country = str(facts.get("country") or "").strip()
    low = q.lower()
    if country and country.lower() not in low:
        q = f"{q} {country}"
    if country and "беларус" in country.lower():
        for token in ():  # public: no regional shop tokens
            if token.lower() not in low:
                q = f"{q} {token}"
    return q[:500]


def extract_search_query(user_text: str) -> str:
    t = (user_text or "").strip()
    if not t:
        return t
    m = re.search(
        r"(?i)(?:найди|найти|поиск|посмотри|всё\s+про|все\s+про|данные\s+про)\s+(.+)",
        t,
    )
    if m:
        return m.group(1).strip()[:400]
    return t[:400]


def apply_pivot_context_hygiene(
    context: Dict[str, Any],
    user_text: str,
    *,
    user_id: str = "",
    group_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not pivot_gate_enabled() or not isinstance(context, dict):
        return context
    tt = context.get("topic_tracking")
    if not isinstance(tt, dict):
        tt = {}
    prior = str(tt.get("current") or "").strip()
    if not topic_pivot(user_text, prior):
        return context
    keep = pivot_recent_keep_count()
    for key in ("recent_dialogue", "recent_messages"):
        rd = context.get(key)
        if isinstance(rd, list) and len(rd) > keep:
            context[key] = rd[-keep:]
    try:
        from core.behavior_store import _topic_from_text

        delta = _topic_from_text((user_text or "").strip(), None)
        if delta:
            tt = dict(tt)
            tt.update(delta)
            context["topic_tracking"] = tt
    except Exception as e:
        logger.debug("pivot topic update: %s", e)
    context["product_behavior_pivot"] = True
    uid = str(user_id or context.get("user_id") or "").strip()
    if uid and _pivot_epoch_bump_enabled():
        try:
            from core.behavior_store import BehaviorStore
            from core.conversation_epoch import bump_conversation_epoch

            _bs = BehaviorStore()
            _rec = _bs.load(uid, group_id)
            bump_conversation_epoch(
                _rec,
                user_id=uid,
                group_id=group_id,
                reason="topic_pivot",
            )
            _bs.save(uid, group_id, _rec)
        except Exception as e:
            logger.debug("pivot conversation_epoch: %s", e)
    if uid and pivot_reset_dialog_enabled():
        try:
            from core.dialog_state import reset_dialog_state

            reset_dialog_state("product_pivot", user_id=uid, group_id=group_id)
        except Exception as e:
            logger.debug("pivot reset_dialog_state: %s", e)
    return context


async def eager_product_search_hint(
    user_text: str,
    user_facts: Optional[Dict[str, Any]],
) -> str:
    if not should_force_product_search(user_text):
        return ""
    q = enrich_search_query(extract_search_query(user_text), user_facts)
    country = ""
    if isinstance(user_facts, dict):
        country = str(user_facts.get("country") or "").strip()
    try:
        from core.universal_search_module import UniversalSearchModule

        pack = await UniversalSearchModule().search(
            q,
            country=country,
            user_id="",
        )
    except Exception as e:
        logger.debug("eager_product_search: %s", e)
        return (
            "SEARCH_CONTRACT: поиск недоступен. Не выдумывай цены и ссылки. "
            "Скажи, что нужно уточнить модель или магазин."
        )
    if not isinstance(pack, dict) or not pack.get("ok"):
        return (
            "SEARCH_CONTRACT: поиск не вернул результатов. "
            "Не выдумывай цены (₽/BYN). Предложи уточнить модель или региональный магазин."
        )
    summary = str(pack.get("summary") or "").strip()
    if not summary:
        return (
            "SEARCH_CONTRACT: пустая выдача. Не выдумывай цены. "
            "Попроси уточнить запрос."
        )
    return (
        "SEARCH_CONTRACT (обязательно опирайся на выдачу; не выдумывай цены и URL):\n"
        f"{summary[:5500]}"
    )


def should_skip_reply_echo_for_user_text(user_text: str) -> bool:
    """Chitchat / пустая реплика — не помечать reply_echo (ложные уроки quality_loop)."""
    ut = (user_text or "").strip()
    if len(ut) < 4:
        return True
    low = ut.lower()
    if re.match(
        r"(?i)^(привет|здравств|hi|hello|как дела|что нового|добрый|доброе|спасибо|ок|да|нет)\b",
        low,
    ):
        return True
    if len(ut) < 28 and "?" not in ut and not re.search(
        r"(?i)\b(почему|зачем|как|что|где|когда|сколько|объясни|перевед|реши)\b",
        low,
    ):
        return True
    return False


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def assistant_reply_issues(
    user_text: str,
    reply: str,
    last_assistant: str = "",
    recent_dialogue: Any = None,
) -> List[str]:
    if not reply_gate_enabled():
        return []
    issues: List[str] = []
    ut = (user_text or "").strip()
    rep = (reply or "").strip()
    prev = (last_assistant or "").strip()
    skip_echo = should_skip_reply_echo_for_user_text(ut)
    if (
        not skip_echo
        and prev
        and len(rep) > 40
        and _similarity(rep, prev) >= float(os.getenv("PRODUCT_BEHAVIOR_ECHO_SIM_THRESHOLD", "0.82"))
    ):
        issues.append("reply_echo")
    u_sci = subject_bucket(ut) == "science"
    r_com = bool(_COMMERCE_REPLY_RE.search(rep))
    u_com = bool(_COMMERCE_RE.search(ut))
    if u_sci and r_com and not u_com:
        issues.append("topic_drift")
    if (
        (cosmology_in_text(ut) or cosmology_thread_active(recent_dialogue))
        and _BOT_SYSTEM_LEAK_RE.search(rep)
    ):
        issues.append("bot_scope_leak")
    return issues


def recover_reply_for_issues(
    user_text: str,
    reply: str,
    issues: List[str],
) -> str:
    if "topic_drift" in issues:
        ut = (user_text or "").strip()
        if subject_bucket(ut) == "science":
            return (
                "Похоже, в ответ попала прошлая тема (магазин/телефон). "
                "Переформулируйте вопрос одной фразой — отвечу по новой теме."
            )
    if "reply_echo" in issues:
        return (
            "Похоже, повторился прошлый ответ. "
            "Задайте вопрос ещё раз короче — отвечу заново."
        )
    if "bot_scope_leak" in issues:
        return (
            "Понял: вы про физическую Вселенную, не про настройки бота. "
            "Повторите вопрос одной фразой — отвечу по космологии."
        )
    return reply
