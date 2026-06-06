"""Policy-dependent memory research on ACC-style scenarios (EASMO / MemoryArena-lite).

Offline only — compares memory construction policies by token recall on needed entities.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

TokenFn = Callable[[str], List[str]]


def tokenize_ru(text: str) -> List[str]:
    return re.findall(r"[\w\u0400-\u04FF]+", (text or "").lower(), re.UNICODE)


def jaccard_tokens(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def recall_tokens(text: str, needed: Set[str], tok: TokenFn = tokenize_ru) -> float:
    if not needed:
        return 1.0
    have = set(tok(text))
    return len(needed & have) / len(needed)


@dataclass
class Turn:
    role: str
    text: str


@dataclass
class Scenario:
    scenario_id: str
    acc_id: str
    turns: List[Turn]
    needed_by_route: Dict[str, Set[str]]
    slots: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryPolicy:
    name: str
    build: Callable[[List[Turn], dict], str]


@dataclass
class AgenticScenario:
    """Multi-turn: recall at final user turn depends on turn 0..N-2."""

    scenario_id: str
    acc_id: str
    turns: List[Turn]
    needed: Set[str]
    slots: Dict[str, Any] = field(default_factory=dict)
    evaluate_after_user_turn: int = -1


def _generic_last_n(n: int) -> MemoryPolicy:
    def build(turns: List[Turn], _: dict) -> str:
        return " ".join(t.text for t in turns[-max(2, n) :])

    return MemoryPolicy(name=f"generic_last_{n}", build=build)


def _full_transcript() -> MemoryPolicy:
    def build(turns: List[Turn], slots: dict) -> str:
        body = " ".join(t.text for t in turns)
        slot_txt = _format_slots(slots)
        return f"{slot_txt} {body}".strip()

    return MemoryPolicy(name="full_transcript_plus_slots", build=build)


def _format_slots(slots: dict) -> str:
    parts: List[str] = []
    for key, val in (slots or {}).items():
        if isinstance(val, dict):
            topic = val.get("topic") or val.get("country") or val.get("query") or val.get("subject")
            if topic:
                parts.append(f"slot:{key}={topic}")
        elif val:
            parts.append(f"slot:{key}={val}")
    return " ".join(parts)


def _slot_aware(keep: int) -> MemoryPolicy:
    def build(turns: List[Turn], slots: dict) -> str:
        parts = [_format_slots(slots)]
        parts.append(" ".join(t.text for t in turns[-max(2, keep) :]))
        return " ".join(p for p in parts if p)

    return MemoryPolicy(name=f"slots_plus_last_{keep}", build=build)


def _route_heuristic(route: str) -> MemoryPolicy:
    def build(turns: List[Turn], slots: dict) -> str:
        if route == "article_followup":
            subj = (slots or {}).get("article_thread", {})
            topic = subj.get("topic") if isinstance(subj, dict) else ""
            chunk = " ".join(
                t.text
                for t in turns
                if any(
                    k in t.text.lower()
                    for k in (
                        "мюнхен",
                        "крым",
                        "аэропорт",
                        "бензин",
                        "беспилотник",
                        "что ещё",
                        "что еще",
                        "подробнее",
                    )
                )
            )
            return f"{topic} {chunk}".strip()
        if route == "facts_confirm":
            chunk = " ".join(
                t.text for t in turns if "запомнить" in t.text.lower() or t.text.strip().lower() == "да"
            )
            return chunk
        if route == "news":
            chunk = " ".join(
                t.text for t in turns if "новост" in t.text.lower() or "поиск" in t.text.lower() or "rss" in t.text.lower()
            )
            return chunk
        if route == "pivot_weather":
            chunk = " ".join(
                t.text
                for t in turns
                if any(k in t.text.lower() for k in ("погод", "минск", "завтра", "weather"))
            )
            return chunk
        if route == "recheck":
            anchor = (slots or {}).get("recheck_anchor", {})
            if isinstance(anchor, dict) and anchor.get("last_user_question"):
                return str(anchor["last_user_question"])
            users = [t.text for t in turns if t.role == "user"]
            return users[-2] if len(users) >= 2 else (users[-1] if users else "")
        if route == "reminder":
            chunk = " ".join(
                t.text for t in turns if any(k in t.text.lower() for k in ("напомни", "мам", "отмен", "напоминан"))
            )
            return chunk
        if route == "wall_clock":
            chunk = " ".join(
                t.text
                for t in turns
                if any(k in t.text.lower() for k in ("часовой", "пояс", "минск", "время", "который час"))
            )
            return chunk
        if route == "image_edit":
            chunk = " ".join(
                t.text for t in turns if any(k in t.text.lower() for k in ("переделай", "фото", "картин", "изображен"))
            )
            img = (slots or {}).get("image_edit_session", {})
            if isinstance(img, dict) and img.get("last_prompt"):
                chunk = f"{img.get('last_prompt')} {chunk}"
            return chunk.strip()
        return " ".join(t.text for t in turns[-6:])

    return MemoryPolicy(name=f"route_{route}", build=build)


_LONG_MUNICH = (
    "Аэропорт Мюнхена закрыли на час из-за подозрительного объекта, "
    "похожего на беспилотник. Bild сообщает о массовых задержках. "
) * 8

_LONG_CRIMEA = (
    "На заправках в Крыму подорожал бензин АИ-95; власти обещают стабилизацию цен. "
    "Жители жалуются на очереди у колонок. "
) * 10

SCENARIOS: List[Scenario] = [
    Scenario(
        scenario_id="acc_article_followup",
        acc_id="ACC-#19",
        turns=[
            Turn("user", _LONG_MUNICH),
            Turn("assistant", "Кратко: аэропорт Мюнхена закрыт, причина — дрон."),
            Turn("user", "Что ещё известно?"),
        ],
        needed_by_route={
            "article_followup": {"мюнхен", "аэропорт", "беспилотник"},
            "news": set(),
        },
        slots={"article_thread": {"topic": "Аэропорт Мюнхена беспилотник"}},
    ),
    Scenario(
        scenario_id="acc_crimea_followup",
        acc_id="ACC-#19",
        turns=[
            Turn("user", _LONG_CRIMEA),
            Turn("assistant", "Кратко: в Крыму подорожал бензин АИ-95, очереди на заправках."),
            Turn("user", "Подробнее про бензин"),
        ],
        needed_by_route={
            "article_followup": {"крым", "бензин", "аи"},
            "news": set(),
        },
        slots={"article_thread": {"topic": "Крым бензин АИ-95"}},
    ),
    Scenario(
        scenario_id="acc_facts_da",
        acc_id="ACC-3",
        turns=[
            Turn("user", "paste: статья про Германию…"),
            Turn("assistant", "Краткий пересказ."),
            Turn("user", "Запомнить страну Германия?"),
            Turn("user", "да"),
        ],
        needed_by_route={
            "facts_confirm": {"запомнить", "германия", "да"},
            "news": set(),
        },
        slots={"pending_facts": {"country": "Германия", "awaiting": "да"}},
    ),
    Scenario(
        scenario_id="acc_pivot_weather",
        acc_id="ACC-1",
        turns=[
            Turn("user", "сколько стоит акция Tesla"),
            Turn("assistant", "Tesla около … USD."),
            Turn("user", "а какая погода завтра в Минске"),
        ],
        needed_by_route={
            "pivot_weather": {"минск", "погод", "завтра"},
            "news": set(),
        },
        slots={},
    ),
    Scenario(
        scenario_id="acc_recheck_anchor",
        acc_id="ACC-9",
        turns=[
            Turn("user", "найди население Галаца"),
            Turn("assistant", "Галац — около … жителей."),
            Turn("user", "перепроверь последний вопрос"),
        ],
        needed_by_route={"recheck": {"галаца", "населен"}},
        slots={"recheck_anchor": {"last_user_question": "население Галаца"}},
    ),
    Scenario(
        scenario_id="acc_reminder_cancel",
        acc_id="ACC-4",
        turns=[
            Turn("user", "напомни через час позвонить маме"),
            Turn("assistant", "Напоминание №1 создано."),
            Turn("user", "отмени напоминание про маму"),
        ],
        needed_by_route={"reminder": {"мам", "напоминан", "отмен"}},
        slots={"active_reminder": {"text": "позвонить маме", "id": "1"}},
    ),
    Scenario(
        scenario_id="acc_news_not_rss",
        acc_id="ACC-7",
        turns=[
            Turn("user", "последние новости про ИИ из интернета, не через rss"),
            Turn("assistant", "Ищу в сети…"),
        ],
        needed_by_route={"news": {"интернет", "rss", "новост"}},
        slots={"user_pref": {"web_over_rss": True}},
    ),
    Scenario(
        scenario_id="acc_wall_clock",
        acc_id="ACC-10",
        turns=[
            Turn("user", "я в Минске, какой сейчас часовой пояс"),
            Turn("assistant", "Europe/Minsk UTC+3"),
        ],
        needed_by_route={"wall_clock": {"минск", "часовой", "пояс"}},
        slots={"geo": {"city": "Минск"}},
    ),
    Scenario(
        scenario_id="acc_image_edit",
        acc_id="IMAGE",
        turns=[
            Turn("user", "[photo] кот на подоконнике"),
            Turn("assistant", "Сгенерировала изображение."),
            Turn("user", "переделай — сделай закат за окном"),
        ],
        needed_by_route={"image_edit": {"переделай", "закат", "окн"}},
        slots={"image_edit_session": {"last_prompt": "кот на подоконнике"}},
    ),
]

AGENTIC_SCENARIOS: List[AgenticScenario] = [
    AgenticScenario(
        scenario_id="agentic_munich_chain",
        acc_id="MemoryArena-lite",
        turns=[
            Turn("user", _LONG_MUNICH),
            Turn("assistant", "Мюнхен: закрытие аэропорта."),
            Turn("user", "а про задержки рейсов?"),
            Turn("assistant", "Массовые задержки по Bild."),
            Turn("user", "что ещё по беспилотнику?"),
        ],
        needed={"мюнхен", "беспилотник", "аэропорт"},
        slots={"article_thread": {"topic": "Мюнхен аэропорт дрон"}},
    ),
    AgenticScenario(
        scenario_id="agentic_facts_then_geo",
        acc_id="ACC-3+10",
        turns=[
            Turn("user", "Запомнить страну Польша?"),
            Turn("assistant", "Подтверди: да/нет"),
            Turn("user", "да"),
            Turn("assistant", "Запомнила Польшу."),
            Turn("user", "какой часовой пояс в Варшаве"),
        ],
        needed={"польш", "варшав", "часовой"},
        slots={"user_facts": {"country": "Польша"}, "geo": {"city": "Варшава"}},
    ),
]


def default_policies(profiles: Dict[str, int]) -> List[MemoryPolicy]:
    return [
        _generic_last_n(profiles.get("standard", 10)),
        _generic_last_n(profiles.get("summarization", 1)),
        _generic_last_n(1),
        _slot_aware(profiles.get("standard", 10)),
        _full_transcript(),
        _route_heuristic("article_followup"),
        _route_heuristic("facts_confirm"),
        _route_heuristic("news"),
        _route_heuristic("pivot_weather"),
        _route_heuristic("recheck"),
        _route_heuristic("reminder"),
        _route_heuristic("wall_clock"),
        _route_heuristic("image_edit"),
    ]


def _scenario_routes(sc: Scenario) -> Dict[str, Set[str]]:
    return sc.needed_by_route


def run_matrix(*, profiles: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    profiles = profiles or {
        "standard": 10,
        "summarization": 1,
        "news_brief": 10,
    }
    policies = default_policies(profiles)

    rows: List[dict] = []
    for sc in SCENARIOS:
        for route, needed in _scenario_routes(sc).items():
            for pol in policies:
                mem = pol.build(sc.turns, sc.slots)
                rows.append(
                    {
                        "scenario": sc.scenario_id,
                        "acc_id": sc.acc_id,
                        "route": route,
                        "policy": pol.name,
                        "recall": round(recall_tokens(mem, needed), 4),
                        "needed": sorted(needed),
                    }
                )

    facts_sc = next(s for s in SCENARIOS if s.scenario_id == "acc_facts_da")
    wrong = _route_heuristic("news").build(facts_sc.turns, {})
    right = _slot_aware(profiles["standard"]).build(facts_sc.turns, facts_sc.slots)
    needed_f = facts_sc.needed_by_route["facts_confirm"]
    wrong_transfer = {
        "news_on_facts_recall": round(recall_tokens(wrong, needed_f), 4),
        "slots_on_facts_recall": round(recall_tokens(right, needed_f), 4),
    }

    article_sc = next(s for s in SCENARIOS if s.scenario_id == "acc_article_followup")
    m_article = _route_heuristic("article_followup").build(article_sc.turns, article_sc.slots)
    m_news = _route_heuristic("news").build(article_sc.turns, article_sc.slots)
    overlap = {
        "article_vs_news_jaccard": round(
            jaccard_tokens(tokenize_ru(m_article), tokenize_ru(m_news)), 4
        ),
    }

    best_by_scenario_route: Dict[str, str] = {}
    for sc in SCENARIOS:
        for route in _scenario_routes(sc):
            key = f"{sc.scenario_id}/{route}"
            candidates = [r for r in rows if r["scenario"] == sc.scenario_id and r["route"] == route]
            if not candidates:
                continue
            best = max(candidates, key=lambda r: r["recall"])
            best_by_scenario_route[key] = best["policy"]

    verdict = _summarize_verdict(rows, wrong_transfer)

    return {
        "hypothesis": "policy-dependent memory beats generic last-N on ACC routes",
        "n_scenarios": len(SCENARIOS),
        "rows": rows,
        "wrong_transfer": wrong_transfer,
        "overlap": overlap,
        "best_policy_per_route": best_by_scenario_route,
        "gemma_profile_recent": profiles,
        "verdict": verdict,
        "agentic": run_agentic_matrix(profiles=profiles),
        "saturation": run_saturation_report(profiles=profiles),
    }


def _summarize_verdict(rows: List[dict], wrong_transfer: dict) -> Dict[str, Any]:
    wins_slots = 0
    checks = 0
    for sc_id in {r["scenario"] for r in rows}:
        article_rows = [
            r
            for r in rows
            if r["scenario"] == sc_id and r["route"] not in ("news", "default") and r["needed"]
        ]
        if not article_rows:
            continue
        route = article_rows[0]["route"]
        subset = [r for r in article_rows if r["route"] == route]
        trim1 = next((r for r in subset if r["policy"] == "generic_last_1"), None)
        slots = next((r for r in subset if r["policy"].startswith("slots_plus")), None)
        if trim1 and slots and slots["recall"] > trim1["recall"]:
            wins_slots += 1
        checks += 1
    return {
        "slots_beats_trim1_scenarios": wins_slots,
        "slots_beats_trim1_total": checks,
        "wrong_transfer_ok": wrong_transfer["news_on_facts_recall"] < 0.5
        and wrong_transfer["slots_on_facts_recall"] >= 0.75,
    }


def run_agentic_matrix(*, profiles: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    profiles = profiles or {"standard": 10, "summarization": 1}
    test_policies = [
        _generic_last_n(1),
        _generic_last_n(profiles["standard"]),
        _slot_aware(profiles["standard"]),
        _route_heuristic("article_followup"),
        _route_heuristic("recheck"),
        _route_heuristic("reminder"),
        _route_heuristic("pivot_weather"),
        _route_heuristic("image_edit"),
    ]
    rows: List[dict] = []
    for sc in AGENTIC_SCENARIOS:
        for pol in test_policies:
            mem = pol.build(sc.turns, sc.slots)
            rows.append(
                {
                    "scenario": sc.scenario_id,
                    "acc_id": sc.acc_id,
                    "policy": pol.name,
                    "recall": round(recall_tokens(mem, sc.needed), 4),
                }
            )
    best: Dict[str, str] = {}
    for sc in AGENTIC_SCENARIOS:
        cand = [r for r in rows if r["scenario"] == sc.scenario_id]
        best[sc.scenario_id] = max(cand, key=lambda r: r["recall"])["policy"]
    return {"rows": rows, "best_policy_per_scenario": best}


def run_saturation_report(*, profiles: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Context Saturation Gap: full transcript vs trim vs slots (Anatomy of Agentic Memory, 2026)."""
    profiles = profiles or {"standard": 10}
    gaps: List[dict] = []
    for sc in SCENARIOS:
        routes = _scenario_routes(sc)
        for route, needed in routes.items():
            if not needed:
                continue
            full = _full_transcript().build(sc.turns, sc.slots)
            trim1 = _generic_last_n(1).build(sc.turns, sc.slots)
            trim_std = _generic_last_n(profiles["standard"]).build(sc.turns, sc.slots)
            slots = _slot_aware(profiles["standard"]).build(sc.turns, sc.slots)
            r_full = recall_tokens(full, needed)
            r_t1 = recall_tokens(trim1, needed)
            r_ts = recall_tokens(trim_std, needed)
            r_sl = recall_tokens(slots, needed)
            gaps.append(
                {
                    "scenario": sc.scenario_id,
                    "route": route,
                    "recall_full": round(r_full, 4),
                    "recall_trim1": round(r_t1, 4),
                    "recall_trim_std": round(r_ts, 4),
                    "recall_slots": round(r_sl, 4),
                    "gap_full_minus_trim1": round(r_full - r_t1, 4),
                    "gap_slots_minus_trim1": round(r_sl - r_t1, 4),
                    "slots_needed": r_sl >= max(r_t1, r_ts) - 0.01,
                }
            )
    n_slots_wins = sum(1 for g in gaps if g["slots_needed"] and g["gap_slots_minus_trim1"] > 0.1)
    return {
        "description": "saturation-aware: when trim loses entities, slots should recover",
        "rows": gaps,
        "slots_recover_count": n_slots_wins,
        "slots_recover_total": len(gaps),
    }


def load_gemma_profiles() -> Optional[Dict[str, int]]:
    try:
        from core.brain.profile_registry import get_profile

        return {
            "standard": get_profile("standard").recent_count,
            "summarization": get_profile("summarization").recent_count,
            "news_brief": get_profile("news_brief").recent_count,
        }
    except Exception:
        return None
