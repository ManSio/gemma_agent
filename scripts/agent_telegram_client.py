#!/usr/bin/env python3
"""
Userbot: отправить сообщение боту в Telegram и дождаться ответа(ов).

  pip install -r requirements-agent.txt
  cp config/agent_telegram.env.example config/agent_telegram.env

  python scripts/agent_telegram_client.py --text "напиши функцию факториала"
  python scripts/agent_telegram_client.py --text "привет" --json-out /tmp/tg.json
  python scripts/agent_telegram_client.py --callback-data factcfm:y
  python scripts/agent_telegram_client.py --suite master_plan_v1

§9 live — только ручной runbook (docs/REFORM_S9_ACCEPTANCE_TRACKER_RU.md).
--suite reform_s9 снят (ложная приёмка: 4 кейса без цепочек «4»/«да»/👎).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_THINKING_RE = re.compile(
    r"(думаю|прошло\s+\d+\s*с|прогноз\s*≈|⏳|typing)",
    re.IGNORECASE,
)
_WELCOME_RE = re.compile(
    r"(привет!\s*я\s+ассистент|пишите\s+обычным\s+языком|справка:\s*/help)",
    re.IGNORECASE,
)
_LEAK_RE = re.compile(
    r"(теперь ответь пользователю|tool_call|если нужно\s*—\s*вызови|"
    r"системный блок закончился|<rule\s+name=|priority=\"override)",
    re.IGNORECASE,
)


def _load_agent_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
    p = _ROOT / "config" / "agent_telegram.env"
    if p.is_file():
        load_dotenv(p, override=True)


def _resolve_telegram_api_credentials() -> tuple[int, str]:
    import re as _re

    raw_id = (
        os.getenv("AGENT_TELEGRAM_API_ID")
        or os.getenv("TELEGRAM_API_ID")
        or ""
    ).strip()
    raw_hash = (
        os.getenv("AGENT_TELEGRAM_API_HASH")
        or os.getenv("TELEGRAM_API_HASH")
        or ""
    ).strip()
    placeholders = {"", "0", "you", "your", "changeme", "xxx"}
    if raw_id.lower() in placeholders or raw_hash.lower() in placeholders:
        raise ValueError(
            "Заполните AGENT_TELEGRAM_API_ID и AGENT_TELEGRAM_API_HASH в "
            "config/agent_telegram.env (ключи: https://my.telegram.org/apps)"
        )
    if not raw_id.isdigit():
        raise ValueError(f"AGENT_TELEGRAM_API_ID должен быть числом, сейчас: {raw_id!r}")
    api_id = int(raw_id)
    if api_id <= 0:
        raise ValueError("AGENT_TELEGRAM_API_ID должен быть > 0")
    if not _re.fullmatch(r"[0-9a-fA-F]{32}", raw_hash):
        raise ValueError(
            f"AGENT_TELEGRAM_API_HASH должен быть 32 hex-символа (сейчас len={len(raw_hash)}). "
            "Скопируйте api_hash целиком с https://my.telegram.org/apps"
        )
    return api_id, raw_hash


def _is_noise_message(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if _LEAK_RE.search(t):
        return True
    if _THINKING_RE.search(t) and len(t) < 120:
        return True
    if _WELCOME_RE.search(t):
        return True
    return False


def _tail_turns(n: int = 2) -> List[Dict[str, Any]]:
    p = _ROOT / "data" / "runtime" / "turns.jsonl"
    if not p.is_file():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


TASK_SUITE: List[Dict[str, Any]] = [
    {
        "id": "factorial",
        "text": "напиши функцию на Python для факториала",
        "check": lambda msgs: any("def " in m or "factorial" in m.lower() for m in msgs),
    },
    {
        "id": "portfolio",
        "text": "смоделируй диверсификацию портфеля из акций и облигаций",
        "check": lambda msgs: any(
            any(w in m.lower() for w in ("акци", "облига", "диверс", "портфел", "%"))
            for m in msgs
        ),
    },
    {
        "id": "math",
        "text": "сколько будет 17*23+5",
        "check": lambda msgs: any("396" in m.replace(" ", "") for m in msgs),
    },
    {
        "id": "short_ok",
        "text": "скажи только: ок",
        "check": lambda msgs: any(m.strip().lower().rstrip(".!,") in ("ок", "ok") for m in msgs),
    },
    {
        "id": "why_earth",
        "text": "Почему земля круглая",
        "check": lambda msgs: any(
            any(w in m.lower() for w in ("земл", "гравит", "сфер", "кругл", "форма"))
            for m in msgs
        )
        and not any(m.strip().lower().rstrip(".!,") in ("ок", "ok") for m in msgs),
    },
]

# Мастер-план §8 — live Telegram (нужен config/agent_telegram.env).
MASTER_PLAN_V1_SUITE: List[Dict[str, Any]] = [
    {
        "id": "v1_cat",
        "text": "Как зовут мою кошку?",
        "check": lambda msgs: any(
            any(w in m.lower() for w in ("кош", "кот", "зовут", "имя", "не знаю", "не помню"))
            for m in msgs
        ),
    },
    {
        "id": "v1_stm",
        "text": "О чём мы говорили в последних сообщениях?",
        "check": lambda msgs: any(len(m.strip()) > 20 for m in msgs),
    },
    {
        "id": "v1_chitchat_math",
        "text": "2+2",
        "check": lambda msgs: any("4" in m.replace(" ", "") for m in msgs),
    },
    {
        "id": "v1_habr_url",
        "text": "https://habr.com/ru/companies/chestnyznak/articles/1037024/",
        "check": lambda msgs: any(len(m.strip()) > 30 for m in msgs)
        and all("/geo_help" not in m.lower() and "math_solve" not in m.lower() for m in msgs),
    },
    {
        "id": "v1_dental",
        "text": (
            "ситуация: зуб гнилой, рядом пломба. рядом с ним второй зуб болит. "
            "Какой план лечения?"
        ),
        "check": lambda msgs: all("/geo_help" not in m.lower() and "geo_help" not in m.lower() for m in msgs),
    },
    {
        "id": "v1_factorial",
        "text": "напиши функцию на Python для факториала",
        "check": lambda msgs: any("```" in m or "def " in m for m in msgs),
    },
]

# Снято 2026-05-30: не воспроизводит §9 (нет multi-turn, leak, /rdel). См. REFORM_S9_ACCEPTANCE_TRACKER_RU.md.
DEPRECATED_SUITES = frozenset({"reform_s9"})


def _deprecated_suite_message(name: str) -> str:
    return (
        f"Suite {name!r} снят: не заменяет §9 live в Example Bot. "
        "Приёмка — docs/REFORM_S9_ACCEPTANCE_TRACKER_RU.md (runbook владельца). "
        "После деплоя: lan_acceptance_smoke.py --deploy-smoke (4 одноходовых, не §9)."
    )


SUITE_CHOICES = {
    "tasks": TASK_SUITE,
    "master_plan_v1": MASTER_PLAN_V1_SUITE,
}


async def _collect_replies(
    client: Any,
    entity: Any,
    *,
    after_msg_id: int,
    timeout_sec: float,
    quiet_sec: float = 4.0,
) -> List[Dict[str, Any]]:
    """Ждать все входящие от бота после our send; пропускать «Думаю…» и утечки."""
    deadline = time.monotonic() + timeout_sec
    seen: set[int] = set()
    collected: List[Dict[str, Any]] = []
    last_new = time.monotonic()

    while time.monotonic() < deadline:
        await asyncio.sleep(2.0)
        batch: List[Dict[str, Any]] = []
        async for msg in client.iter_messages(entity, limit=12):
            if msg.out or msg.id <= after_msg_id or msg.id in seen:
                continue
            body = (msg.text or msg.message or "").strip()
            if not body or _is_noise_message(body):
                seen.add(msg.id)
                continue
            batch.append(
                {
                    "id": msg.id,
                    "date": msg.date.isoformat() if msg.date else "",
                    "text": body,
                }
            )
            seen.add(msg.id)

        if batch:
            batch.sort(key=lambda x: x["id"])
            for row in batch:
                if row not in collected:
                    collected.append(row)
            last_new = time.monotonic()
        elif collected and (time.monotonic() - last_new) >= quiet_sec:
            break

    return collected


async def _click_callback(
    client: Any,
    entity: Any,
    callback_data: str,
    *,
    msg_id: Optional[int] = None,
) -> bool:
    target = None
    async for msg in client.iter_messages(entity, limit=15):
        if msg.out or not msg.reply_markup:
            continue
        if msg_id is not None and msg.id != msg_id:
            continue
        target = msg
        break
    if target is None:
        return False

    data = callback_data.strip()
    try:
        if hasattr(target, "click"):
            # Telethon: click by callback_data bytes
            await target.click(data=data.encode() if isinstance(data, str) else data)
            return True
    except TypeError:
        pass
    try:
        for i, row in enumerate(target.reply_markup.rows):
            for j, btn in enumerate(row.buttons):
                cb = getattr(btn, "data", None)
                if cb is None:
                    continue
                raw = cb.decode("utf-8", errors="replace") if isinstance(cb, bytes) else str(cb)
                if raw == data:
                    await target.click(i=i, j=j)
                    return True
    except Exception:
        pass
    return False


async def _run_turn(
    client: Any,
    entity: Any,
    *,
    text: Optional[str],
    callback_data: Optional[str],
    timeout_sec: float,
) -> Dict[str, Any]:
    after_id = 0
    sent_text = ""
    if text:
        sent = await client.send_message(entity, text)
        after_id = int(sent.id)
        sent_text = text
        print(f"→ Отправлено: {text[:120]}{'…' if len(text) > 120 else ''}")

    if callback_data:
        ok = await _click_callback(client, entity, callback_data)
        print(f"→ Callback {callback_data}: {'ok' if ok else 'кнопка не найдена'}")
        if not ok:
            return {
                "ok": False,
                "error": f"callback not found: {callback_data}",
                "sent_text": sent_text,
                "replies": [],
            }

    replies = await _collect_replies(
        client, entity, after_msg_id=after_id, timeout_sec=timeout_sec
    )
    for r in replies:
        print(f"\n← ({r['date']}) id={r['id']}:\n{r['text'][:2000]}\n")

    return {
        "ok": bool(replies),
        "sent_text": sent_text,
        "callback_data": callback_data,
        "replies": replies,
        "reply_count": len(replies),
        "turns_tail": _tail_turns(3),
    }


async def _run(
    *,
    text: Optional[str],
    callback_data: Optional[str],
    timeout_sec: float,
    phone: str | None,
    suite: Optional[str],
    json_out: Optional[str],
) -> int:
    try:
        from telethon import TelegramClient
    except ImportError:
        print("Установите: pip install -r requirements-agent.txt", file=sys.stderr)
        return 2

    if not text and not callback_data and not suite:
        print("Укажите --text, --callback-data или --suite", file=sys.stderr)
        return 2

    if suite in DEPRECATED_SUITES:
        print(_deprecated_suite_message(suite), file=sys.stderr)
        return 2

    _load_agent_env()
    try:
        api_id, api_hash = _resolve_telegram_api_credentials()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    bot_user = (os.getenv("AGENT_TARGET_BOT_USERNAME") or "").strip().lstrip("@")
    if not bot_user:
        print("Задайте AGENT_TARGET_BOT_USERNAME в config/agent_telegram.env", file=sys.stderr)
        return 2

    phone = (phone or os.getenv("AGENT_TELEGRAM_PHONE") or "").strip() or None
    session_path = str(_ROOT / "data" / "agent" / "telegram")
    (_ROOT / "data" / "agent").mkdir(parents=True, exist_ok=True)

    client = TelegramClient(session_path, api_id, api_hash)
    try:
        await client.start(phone=phone)
    except Exception as e:
        if "ApiIdInvalidError" in type(e).__name__:
            print("Неверные api_id/api_hash — my.telegram.org/apps", file=sys.stderr)
            return 2
        raise

    entity = await client.get_entity(bot_user)
    report: Dict[str, Any] = {"bot": bot_user, "tests": []}

    try:
        if suite:
            items = SUITE_CHOICES.get(suite) or []
            if not items:
                print(f"Неизвестный suite: {suite}", file=sys.stderr)
                return 2
            passed = 0
            for item in items:
                print(f"\n{'='*50}\nSUITE {item['id']}\n{'='*50}")
                row = await _run_turn(
                    client,
                    entity,
                    text=item["text"],
                    callback_data=None,
                    timeout_sec=timeout_sec,
                )
                texts = [r["text"] for r in row.get("replies") or []]
                ok = bool(texts) and item["check"](texts)
                row["suite_id"] = item["id"]
                row["pass"] = ok
                report["tests"].append(row)
                if ok:
                    passed += 1
                print(f"{'PASS' if ok else 'FAIL'}: {item['id']}")
                await asyncio.sleep(8.0)
            report["summary"] = {"pass": passed, "total": len(items), "suite": suite}
            print(f"\n--- Итого {suite}: {passed}/{len(items)} ---")
            code = 0 if passed == len(items) else 1
        else:
            row = await _run_turn(
                client,
                entity,
                text=text,
                callback_data=callback_data,
                timeout_sec=timeout_sec,
            )
            report = row
            code = 0 if row.get("ok") else 1
    finally:
        await client.disconnect()

    if json_out:
        Path(json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return code


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="")
    ap.add_argument("--callback-data", default="", help="Напр. factcfm:y (кнопка «Да»)")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--phone", default="")
    ap.add_argument(
        "--suite",
        choices=sorted(set(SUITE_CHOICES.keys()) | set(DEPRECATED_SUITES)),
        help="tasks | master_plan_v1 (reform_s9 снят — см. REFORM_S9_ACCEPTANCE_TRACKER_RU.md)",
    )
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    return asyncio.run(
        _run(
            text=(args.text or "").strip() or None,
            callback_data=(args.callback_data or "").strip() or None,
            timeout_sec=args.timeout,
            phone=args.phone or None,
            suite=args.suite,
            json_out=(args.json_out or "").strip() or None,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
