"""
Общая логика честной приёмки реформы (orchestrator / reminders).

Не заменяет Telegram §9 — см. docs/REFORM_S9_ACCEPTANCE_TRACKER_RU.md.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Метки для ops (не называть «§9 закрыто»).
REFORM_ROUTE_REGRESSION_ID = "reform_route_regression"
REFORM_CHAIN_PROBE_ID = "reform_chain_probe"


def reply_blob(result: Dict[str, Any]) -> str:
    parts: List[str] = []
    for row in result.get("outputs") or []:
        if isinstance(row, dict) and row.get("type") == "text":
            parts.append(str(row.get("payload") or ""))
    if not parts:
        parts.extend(str(x) for x in (result.get("telegram_messages") or []))
    if not parts and result.get("assistant_text"):
        parts.append(str(result["assistant_text"]))
    return "\n".join(parts).strip()


_CORRECTION_ACK_RE = re.compile(
    r"^📝\s*Учту вашу правку[^\n]*\n+",
    re.IGNORECASE | re.MULTILINE,
)
_EMPTY_GUARD_RE = re.compile(r"пустой ответ после обработки", re.IGNORECASE)
_COUNTRY_CONFIRM_RE = re.compile(r"запомнить\s+(?:страну|насел)", re.IGNORECASE)
_IDLE_ACK_RE = re.compile(r"^(?:запомнил|уже записано)\.?\s*$", re.IGNORECASE)
_SEARCH_FAIL_RE = re.compile(
    r"внутренн(?:яя|ей)\s+ошибк(?:а|и)\s+поиск|запрос не был передан|точных данных сейчас нет",
    re.IGNORECASE,
)
_GOOGLE_NEWS_META_RE = re.compile(
    r"google\s+новости\s*-\s*в\s+мире|приложени[ея]\s+[\"']?google\s+новости",
    re.IGNORECASE,
)


def substantive_reply_text(blob: str) -> str:
    """Текст ответа без префикса «учту правку» (substance для проверок)."""
    s = (blob or "").strip()
    if not s:
        return ""
    return _CORRECTION_ACK_RE.sub("", s, count=1).strip()


def looks_truncated_reply(blob: str, *, min_len: int = 160) -> bool:
    """Обрезка: длинный ответ без финальной пунктуации и с обрывом на коротком хвосте."""
    s = substantive_reply_text(blob)
    if len(s) < min_len:
        return False
    if s.endswith("..."):
        return False
    if s[-1] in ".!?…»\"')]}":
        return False
    # «…механическая р», «…и пос»
    if re.search(r"[\s—\-]\S{1,3}$", s):
        return True
    if re.search(r"[\wа-яё]$", s, re.IGNORECASE) and len(s.split()[-1]) <= 2:
        return True
    return False


def validate_baseline_live_reply(blob: str, user_text: str = "") -> List[str]:
    """Общие FAIL: пусто, guard, fallback, leak, country confirm, search error."""
    errs: List[str] = []
    b = (blob or "").strip()
    if not b:
        return ["empty_reply"]
    if _EMPTY_GUARD_RE.search(b):
        errs.append("empty_guard")
    if _COUNTRY_CONFIRM_RE.search(b):
        errs.append("country_confirm_leak")
    if _SEARCH_FAIL_RE.search(b):
        errs.append("search_internal_error")
    if _IDLE_ACK_RE.match(substantive_reply_text(b)):
        errs.append("idle_ack_only")
    if looks_truncated_reply(b):
        errs.append("truncated_reply")
    try:
        from core.agent_test_validators import validate_reply

        errs.extend(validate_reply(b, user_text, {"validators": ["no_fallback", "no_leak"]}))
    except Exception as e:
        logger.debug("validate_baseline validate_reply: %s", e)
    return errs


def validate_news_world_reply(blob: str) -> List[str]:
    """«Какие новости» — нумерованный дайджест, не meta Google RSS и не обрубок."""
    errs: List[str] = list(validate_baseline_live_reply(blob, "Какие новости в мире"))
    s = substantive_reply_text(blob)
    if len(s) < 80:
        errs.append(f"news_digest_short:{len(s)}")
    if _GOOGLE_NEWS_META_RE.search(s):
        errs.append("news_google_meta_dump")
    has_numbered = bool(re.search(r"(?m)^\d+\.\s+\S", s))
    has_narrative = (
        len(s) >= 200
        and s.count("\n\n") >= 1
        and not re.search(r"новости по теме:", s, re.I)
        and not re.search(r"новости\s+беларуси\s*\|\s*белта", s, re.I)
    )
    if not has_numbered and not has_narrative:
        errs.append("news_no_numbered_digest")
    if re.search(r"ленту rss|google news rss", s, re.I):
        errs.append("news_rss_leak")
    return errs


def validate_news_pick_item_reply(blob: str) -> List[str]:
    """«4» после дайджеста — развёрнутое содержание пункта, не повтор заголовка."""
    errs: List[str] = list(validate_baseline_live_reply(blob, "4"))
    s = substantive_reply_text(blob)
    if len(s) < 50:
        errs.append(f"news_pick4_short:{len(s)}")
    if "не вижу свежего списка" in s.lower():
        errs.append("news_pick4_no_digest")
    if looks_truncated_reply(s, min_len=80):
        errs.append("news_pick4_truncated")
    return errs


def validate_paste_article_reply(blob: str) -> List[str]:
    """Paste статьи — пересказ, не confirm страны и не «ошибка поиска»."""
    errs: List[str] = list(validate_baseline_live_reply(blob))
    s = substantive_reply_text(blob).lower()
    if len(s) < 80:
        errs.append(f"paste_short:{len(s)}")
    if not re.search(r"тихановск|обращен|перемен|граждан", s):
        errs.append("paste_no_article_substance")
    return errs


def validate_recheck_followup(blob: str) -> List[str]:
    errs: List[str] = list(validate_baseline_live_reply(blob, "Может ты хорошо посмотришь"))
    s = substantive_reply_text(blob).lower()
    if "галат" in s:
        errs.append("wrong_context_galats")
    if not re.search(r"ноль|0|нет.*букв", s):
        errs.append("recheck_no_zero_answer")
    return errs


def validate_incident_followup(blob: str, prior_context: str) -> List[str]:
    errs: List[str] = list(validate_baseline_live_reply(blob))
    s = substantive_reply_text(blob).lower()
    if len(s) < 25:
        errs.append(f"incident_short:{len(s)}")
    ctx = prior_context.lower()
    if "api" in ctx or "деплой" in ctx or "panel_nohup" in ctx:
        if re.search(r"уточни.*(?:каком|какой)\s+(?:именно\s+)?(?:событ|инцидент)", s):
            errs.append("incident_ignored_dialogue_context")
    return errs


def validate_correction_followup(blob: str, prior_reply: str) -> List[str]:
    errs: List[str] = list(validate_baseline_live_reply(blob, "не так"))
    sub = substantive_reply_text(blob)
    if len(sub) < 20:
        errs.append("correction_short")
    if len(prior_reply) > 80 and len(sub) >= len(prior_reply) * 0.95:
        errs.append("correction_not_shorter")
    return errs


def validate_affirmative_search_turn2(blob: str) -> List[str]:
    errs: List[str] = list(validate_baseline_live_reply(blob, "да"))
    s = substantive_reply_text(blob)
    if re.search(r"уже записано|запомнил", s, re.I):
        errs.append("aff_idle_ack")
    if len(s) < 30 and not re.search(r"поиск|найден|http|хлестов|новост", s, re.I):
        errs.append(f"aff_turn2_weak:{s[:80]}")
    return errs


def behavior_dir(root: Optional[Path] = None) -> Path:
    base = root or Path(__file__).resolve().parents[1]
    return base / "data" / "users" / "behavior"


def cleanup_probe_behavior(
    user_id: str,
    *,
    root: Optional[Path] = None,
    also_prefix: Optional[str] = None,
) -> int:
    """Удалить json probe-пользователя (и *.reform.* если also_prefix задан)."""
    bdir = behavior_dir(root)
    if not bdir.is_dir():
        return 0
    removed = 0
    uid = str(user_id).strip()
    candidates = [bdir / f"{uid}__dm.json"]
    if also_prefix:
        for p in bdir.glob(f"{also_prefix}*__dm.json"):
            candidates.append(p)
    for p in bdir.glob(f"{uid}.reform.*__dm.json"):
        candidates.append(p)
    seen: set[str] = set()
    for p in candidates:
        key = str(p.resolve())
        if key in seen or not p.is_file():
            continue
        seen.add(key)
        try:
            p.unlink()
            removed += 1
        except OSError as e:
            logger.debug("cleanup_probe_behavior %s: %s", p, e)
    return removed


async def run_rdel_acceptance_chain(
    user_id: str,
    *,
    timeout: float,
    channel: str,
    run_probe,
) -> List[str]:
    """§9 #10: /radd → /rdel 1 через тот же orchestrator, что в Telegram."""
    from modules.light_reminders.module import LightRemindersModule

    mod = LightRemindersModule()
    ctx = {"user_id": user_id}
    add = await mod.execute({"input": {"payload": "/radd 1 reform probe rdel"}, "context": ctx})
    if not str(add.payload or "").strip():
        return ["rdel:radd_empty"]
    try:
        result = await asyncio.wait_for(
            run_probe(
                user_id=user_id,
                text="/rdel 1",
                group_id=None,
                channel=channel,
                bug_pending=False,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return [f"rdel:timeout:{int(timeout)}s"]
    except Exception as e:
        return [f"rdel:probe:{e}"]
    blob = reply_blob(result)
    if re.search(r"удален", blob, re.IGNORECASE) or "Удалено" in blob:
        return []
    return [f"rdel:unexpected_reply:{blob[:120]}"]


def validate_weather_reply(blob: str) -> List[str]:
    errs: List[str] = list(validate_baseline_live_reply(blob, "погода"))
    s = substantive_reply_text(blob)
    if len(s) < 15:
        errs.append(f"weather_short:{len(s)}")
    if re.search(r"напишите город|укажите город", s, re.I):
        errs.append("weather_ask_city")
    if not re.search(r"°|град|прогноз|ветер|осадк|облач", s, re.I):
        errs.append("weather_no_forecast_markers")
    if "минск" not in s.lower() and "minsk" not in s.lower():
        errs.append("weather_not_minsk")
    return errs


def validate_philosophy_reply(blob: str) -> List[str]:
    errs: List[str] = list(validate_baseline_live_reply(blob))
    s = substantive_reply_text(blob)
    if len(s) < 25:
        errs.append(f"philosophy_short:{len(s)}")
    if re.search(r"погода в|°C\s*в Минске|напишите город", s, re.I):
        errs.append("philosophy_weather_leak")
    if not re.search(r"кант|сартр|свобод|вол", s, re.I):
        errs.append("philosophy_no_topic_markers")
    if looks_truncated_reply(s, min_len=120):
        errs.append("philosophy_truncated")
    return errs


def validate_news_reply_no_rss_leak(blob: str) -> List[str]:
    """Ответ на новости не должен ссылаться на Google News RSS / ленту."""
    errs: List[str] = []
    s = substantive_reply_text(blob)
    for pat in (
        r"Google News RSS",
        r"ленту rss",
        r"news\.google\.com",
        r"не удалось подтянуть свежие заголовки из ленты",
    ):
        if re.search(pat, s, re.I):
            errs.append(f"news_rss_leak:{pat}")
    return errs


def validate_news_not_rss(blob: str) -> List[str]:
    """Legacy alias — только проверка утечки RSS в ответе (кейс «не rss» снят с acceptance)."""
    return validate_news_reply_no_rss_leak(blob)


def validate_pending_correction(rec: Dict[str, Any]) -> List[str]:
    rp = rec.get("routing_prefs") if isinstance(rec.get("routing_prefs"), dict) else {}
    pending = rp.get("pending_correction")
    if not isinstance(pending, dict):
        return ["correction:no_pending_correction"]
    if not (pending.get("instruction") or pending.get("text")):
        return ["correction:empty_instruction"]
    if int(pending.get("turns_left") or 0) < 1:
        return ["correction:turns_left_zero"]
    return []
