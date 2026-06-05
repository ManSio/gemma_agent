#!/usr/bin/env python3
"""
Собрать корпус тестов: архивы message_archive + регрессия + синтетика до --target N.

  python scripts/build_test_corpus.py --root . --target 1000
  python scripts/build_test_corpus.py --root /opt/gemma_agent --out data/testing/corpus.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

sys.path.insert(0, str(_ROOT / "scripts"))
from correlate_archives_logs import _archive_dir, _load_archives, fingerprint  # noqa: E402
from probe_user_id import default_probe_user_id  # noqa: E402


def _wants_image_generation(text: str) -> bool:
    """Платная генерация — не тащим из архива в массовый прогон."""
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    if low.startswith("/imagine") or low.startswith("imagine "):
        return True
    try:
        from core.image_gen_nl import prose_wants_image_generation

        return bool(prose_wants_image_generation(t))
    except Exception:
        return bool(
            re.search(
                r"(?i)(сгенерир\w*\s+изображ|/imagine|нарисуй\s+картин)",
                t,
            )
        )


def _regression_cases() -> List[Dict[str, Any]]:
    return [
        {
            "id": "reg_factorial",
            "source": "regression",
            "text": "напиши функцию на Python для факториала",
            "validators": ["no_fallback", "no_leak", "not_empty", "expect_regex"],
            "expect_regex": r"(def\s+\w+|factorial|факториал)",
            "tags": ["code"],
        },
        {
            "id": "reg_portfolio",
            "source": "regression",
            "text": "смоделируй диверсификацию портфеля из акций и облигаций",
            "validators": ["no_fallback", "no_leak", "not_empty", "expect_regex"],
            "expect_regex": r"(?i)(портфел|диверс|акци|облига|распредел|30/70|50/50|etf|офз)",
            "expect_not_contains": ["запомнить населённый пункт"],
            "tags": ["finance"],
        },
        {
            "id": "reg_math",
            "source": "regression",
            "text": "сколько будет 17*23+5",
            "validators": ["no_fallback", "no_leak", "not_empty", "expect_regex"],
            "expect_regex": r"396",
            "tags": ["math"],
        },
        {
            "id": "reg_short_ok",
            "source": "regression",
            "text": "скажи только: ок",
            "validators": ["no_fallback", "no_leak", "expect_regex"],
            "expect_regex": r"^(ок|ok)\s*[\.\!]?\s*$",
            "tags": ["brief"],
        },
        {
            "id": "reg_why_earth",
            "source": "regression",
            "text": "Почему земля круглая",
            "validators": [
                "no_fallback",
                "no_leak",
                "not_empty",
                "no_trivial_ack_on_explain",
                "expect_regex",
            ],
            "expect_regex": r"(?i)(земл|гравит|сфер|кругл|капл|шар|форма)",
            "tags": ["explain"],
        },
        {
            "id": "reg_reminder_prose",
            "source": "regression",
            "text": (
                "В статье про AI-агентов автор пишет о важности конкретного напоминания "
                "о дедлайне для команды разработки."
            ),
            "validators": ["no_fallback", "no_leak", "not_empty"],
            "expect_not_contains": ["создал напоминание", "напоминание создано"],
            "tags": ["reminder_trap"],
        },
        {
            "id": "reg_bug_pending",
            "source": "regression",
            "text": "мусор",
            "bug_pending": True,
            "validators": ["no_leak"],
            "expect_not_contains": ["баг-репорт", "bug report"],
            "tags": ["bug"],
        },
        {
            "id": "reg_image_gen",
            "source": "regression",
            "text": "нарисуй простой красный круг на белом фоне",
            "allow_image_gen": True,
            "validators": ["no_fallback", "no_leak"],
            "tags": ["image_gen", "paid_smoke"],
        },
        # Инцидент 2026-05-21: Habr / длинная вставка / привет — только маршрут (быстро, без LLM).
        {
            "id": "reg_incident_habr_url",
            "source": "regression",
            "route_only": True,
            "text": "https://habr.com/ru/companies/chestnyznak/articles/1037024/",
            "validators": ["check_preflight_profile", "check_clamp_profile"],
            "expect_preflight_profile": "summarization",
            "expect_clamp_from_profile": "math_solve",
            "expect_clamp_to_profile": "summarization",
            "tags": ["incident_20260521", "habr", "route"],
        },
        {
            "id": "reg_incident_rag_paste",
            "source": "regression",
            "route_only": True,
            "text": (
                "Сейчас в проекте реализовано несколько идей из статьи про RAG-систему. "
                "experience_digest.jsonl и strategy_paths. "
                "Модуль reputation/ считает v_c для маршрутов. "
                "Qdrant для фактов. Скиллы math_reasoning и UrlFetch. "
                "Метрики RAGAS и golden_dataset. "
                "OpenRouter и API-ключ в .env.example — для админа. " * 4
            ),
            "validators": [
                "check_clamp_profile",
                "check_not_operational_diag",
            ],
            "expect_clamp_from_profile": "math_solve",
            "expect_clamp_to_profile": "quick_explain",
            "tags": ["incident_20260521", "long_paste", "route"],
        },
        {
            "id": "reg_incident_greeting",
            "source": "regression",
            "route_only": True,
            "text": "Приветик",
            "validators": ["check_preflight_profile", "check_not_operational_diag"],
            "expect_preflight_profile": "__none__",
            "tags": ["incident_20260521", "greeting", "route"],
        },
        {
            "id": "reg_v1_dental_geo_gate",
            "source": "regression",
            "route_only": True,
            "text": (
                "ситуация такая один зуб гнилой нужно удалять. рядом с ним зуб с хроническим "
                "пульпитом пролечили. Какой план лечения?"
            ),
            "validators": ["check_gate_verdict"],
            "expect_gate_rule_id": "geo_nearby",
            "expect_gate_verdict": "blocked",
            "tags": ["master_plan_v1", "dental", "geo", "route"],
        },
        {
            "id": "reg_v1_code_error_prose_clamp",
            "source": "regression",
            "route_only": True,
            "text": "после лечения пульпита осталась ошибка в прикусе, зуб болит",
            "validators": ["check_clamp_profile"],
            "expect_clamp_from_profile": "code_debug",
            "expect_clamp_to_profile": "quick_explain",
            "tags": ["master_plan_v1", "code", "route"],
        },
        {
            "id": "reg_v1_habr_embedded_long_prose",
            "source": "regression",
            "route_only": True,
            "text": (
                "Обсуждали архитектуру бота и память диалога. " * 12
                + "Ссылка для контекста https://habr.com/ru/companies/chestnyznak/articles/1037024/ "
                + "Какой профиль роутера лучше для chitchat?"
            ),
            "validators": ["check_preflight_profile"],
            "expect_preflight_profile": "__none__",
            "tags": ["master_plan_v1", "habr", "route"],
        },
    ]


def _acc_dialog_chain_cases() -> List[Dict[str, Any]]:
    """Поток H3 (ACC): ≥2 реплики в одном кейсе — см. agent_test_runner dialog_turns."""
    return [
        {
            "id": "reg_chain_acc1_pivot_finance_physics",
            "source": "regression",
            "dialog_turns": [
                "коротко: чем диверсифицировать портфель акций и облигаций на 5 лет?",
                "стоп, другая тема: почему небо голубое — одним абзацем",
            ],
            "text": "стоп, другая тема: почему небо голубое — одним абзацем",
            "validators": ["no_fallback", "no_leak", "not_empty", "expect_regex"],
            "expect_regex": r"(?i)(небо|голуб|рассеив|реле|солнеч|луч|атмосф)",
            "tags": ["acc_chain", "ACC-1", "pivot", "h3_20260526"],
        },
        {
            "id": "reg_chain_acc_math_followup",
            "source": "regression",
            "dialog_turns": [
                "сколько будет 11*13, ответь только числом",
                "к тому числу прибавь 7, снова только число",
            ],
            "text": "к тому числу прибавь 7, снова только число",
            "validators": ["no_fallback", "no_leak", "not_empty", "expect_regex"],
            "expect_regex": r"\b150\b",
            "tags": ["acc_chain", "math", "context", "h3_20260526"],
        },
        {
            "id": "reg_chain_acc_chitchat_then_math",
            "source": "regression",
            "dialog_turns": [
                "привет, как дела?",
                "числом: сколько будет 8*7",
            ],
            "text": "числом: сколько будет 8*7",
            "validators": ["no_fallback", "no_leak", "not_empty", "expect_regex"],
            "expect_regex": r"\b56\b",
            "tags": ["acc_chain", "chitchat", "math", "h3_20260526"],
        },
    ]


def _reform_acceptance_cases() -> List[Dict[str, Any]]:
    """Лист приёмки §9 BRAIN_CENTRIC_REFORM_PLAN — route_only / planner_direct."""
    tikh = (
        "Тихановская выступила с обращением к гражданам. "
        "Она заявила о необходимости перемен в стране. " * 12
        + "\n\nЧитайте также на myfin.by подробнее о событиях."
    )
    philosophy = (
        "Свобода воли и ответственность — не противоречие, а два измерения одного выбора. "
        "Кант отделял явления от вещей в себе; Сартр говорил, что человек осуждён быть свободным. "
        "В современной нейронауке спорят, иллюзия ли свобода воли или эмерджентное свойство. "
        "Как вы это видите?"
    )
    return [
        {
            "id": "reform_news_world",
            "source": "regression",
            "route_only": True,
            "text": "Какие новости в мире",
            "validators": ["check_planner_direct"],
            "expect_planner_direct_kind": "news",
            "expect_planner_direct_allowed": False,
            "tags": ["reform_20260525", "news", "route"],
        },
        {
            "id": "reform_weather_minsk",
            "source": "regression",
            "route_only": True,
            "text": "погода в Минске",
            "validators": ["check_planner_direct"],
            "expect_planner_direct_kind": "weather",
            "expect_planner_direct_allowed": False,
            "tags": ["reform_20260525", "weather", "route"],
        },
        {
            "id": "reform_paste_tikhovskaya_preflight",
            "source": "regression",
            "route_only": True,
            "text": tikh,
            "validators": ["check_preflight_profile", "check_clamp_profile"],
            "expect_preflight_profile": "quick_explain",
            "expect_clamp_from_profile": "math_solve",
            "expect_clamp_to_profile": "quick_explain",
            "tags": ["reform_20260525", "paste", "route"],
        },
        {
            "id": "reform_philosophy_not_weather",
            "source": "regression",
            "route_only": True,
            "text": philosophy,
            "validators": ["check_planner_direct", "check_preflight_profile"],
            "expect_planner_direct_kind": "weather",
            "expect_planner_direct_allowed": False,
            "expect_preflight_profile": "__none__",
            "tags": ["reform_20260525", "prose", "route"],
        },
        {
            "id": "reform_affirmative_yes_blocked",
            "source": "regression",
            "route_only": True,
            "text": "да",
            "validators": ["check_planner_direct"],
            "expect_planner_direct_kind": "affirmative_search",
            "expect_planner_direct_allowed": False,
            "tags": ["reform_20260525", "affirmative", "route"],
        },
        {
            "id": "reform_rdel_command",
            "source": "regression",
            "route_only": True,
            "text": "/rdel 1",
            "validators": ["check_preflight_profile"],
            "expect_preflight_profile": "__none__",
            "tags": ["reform_20260525", "reminder", "route"],
        },
    ]


def _route_example_file_cases() -> List[Dict[str, Any]]:
    try:
        from core.route_example_store import load_route_examples, route_example_to_corpus_case

        return [route_example_to_corpus_case(r) for r in load_route_examples()]
    except Exception:
        return []


def _synthetic_cases(seed: int, count: int) -> Iterator[Dict[str, Any]]:
    rng = random.Random(seed)
    templates = [
        ("math_{a}_{b}", "сколько будет {a}*{b}+{c}", r"{result}", ["math"]),
        ("translate_en", "переведи на английский: {phrase}", None, ["translate"]),
        ("code_hello", "напиши hello world на Python", r"(print|def)", ["code"]),
        ("why_sky", "почему небо голубое", None, ["explain"]),
        ("factorial_n", "напиши функцию факториала для n", r"(def|factorial)", ["code"]),
    ]
    phrases = ["добрый день", "как дела", "спасибо", "до свидания", "хорошо"]
    n = 0
    while n < count:
        tpl_id, text_tpl, regex, tags = rng.choice(templates)
        if "{a}" in text_tpl:
            a, b, c = rng.randint(2, 40), rng.randint(2, 40), rng.randint(0, 9)
            result = a * b + c
            text = text_tpl.format(a=a, b=b, c=c)
            expect_regex = regex.format(result=result) if regex else None
            cid = f"syn_{tpl_id}_{a}_{b}_{c}"
        elif "{phrase}" in text_tpl:
            phrase = rng.choice(phrases)
            text = text_tpl.format(phrase=phrase)
            expect_regex = regex
            cid = f"syn_{tpl_id}_{fingerprint(phrase)[:8]}"
        else:
            text = text_tpl
            expect_regex = regex
            cid = f"syn_{tpl_id}_{n}"
        case: Dict[str, Any] = {
            "id": cid,
            "source": "synthetic",
            "text": text,
            "validators": ["no_fallback", "no_leak", "not_empty"],
            "tags": tags,
        }
        if expect_regex:
            case["validators"].append("expect_regex")
            case["expect_regex"] = expect_regex
        yield case
        n += 1


def _archive_cases(root: Path, default_user_id: str) -> List[Dict[str, Any]]:
    arch = _load_archives(_archive_dir(root))
    seen: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for uid, turns in arch.items():
        for t in turns:
            if t.get("role") != "user":
                continue
            text = (t.get("text") or "").strip()
            if len(text) < 3 or len(text) > 2000:
                continue
            if text.lower().startswith("/benchmark_run"):
                continue
            if _wants_image_generation(text):
                continue
            fp = fingerprint(text)
            if not fp or fp in seen:
                continue
            seen.add(fp)
            out.append(
                {
                    "id": f"arch_{uid}_{fp[:8]}",
                    "source": "archive",
                    "archive_user_id": uid,
                    "user_id": default_user_id,
                    "text": text,
                    "validators": ["no_fallback", "no_leak", "not_empty"],
                    "tags": ["archive", t.get("group") or "dm"],
                }
            )
    return out


def build_corpus(
    root: Path,
    *,
    target: int,
    default_user_id: str,
    seed: int,
) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    cases.extend(_regression_cases())
    cases.extend(_acc_dialog_chain_cases())
    cases.extend(_reform_acceptance_cases())
    cases.extend(_route_example_file_cases())
    cases.extend(_archive_cases(root, default_user_id))
    need = max(0, target - len(cases))
    cases.extend(list(_synthetic_cases(seed, need)))
    # stable order: regression first, then archive, synthetic
    return cases[:target]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(_ROOT))
    ap.add_argument("--out", default="data/testing/corpus.jsonl")
    ap.add_argument("--target", type=int, default=1000)
    ap.add_argument(
        "--user-id",
        default=default_probe_user_id() or "900000001",
        help="user_id в кейсах (env POST_DEPLOY_PROBE_USER_ID / OWNER_TELEGRAM_ID)",
    )
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    root = Path(args.root)
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    cases = build_corpus(root, target=args.target, default_user_id=args.user_id, seed=args.seed)
    with out.open("w", encoding="utf-8") as f:
        for c in cases:
            if "user_id" not in c:
                c["user_id"] = args.user_id
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    stats = {}
    for c in cases:
        stats[c.get("source", "?")] = stats.get(c.get("source", "?"), 0) + 1
    print(json.dumps({"out": str(out), "total": len(cases), "by_source": stats}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
