#!/usr/bin/env python3
"""
Полная офлайн-проверка перед деплоем (без правок ядра логики бота).

Делает:
  1) Структура репозитория и module.json плагинов (как verify_structure.py)
  2) Валидация настроек AppConfig (лимиты, голос и т.д.)
  3) Наличие критичных переменных окружения (предупреждения, не фейл CI)
  4) pytest tests/
  5) unittest для modules/*/tests.py и core_libraries/*/tests.py

Самовосстановление до «стабильного» состояния без смены кода:
  • resilience + autonomy + обслуживание (SELF_MAINTENANCE) снимают бэкапы и режут safe mode при восстановлении метрик;
  • при залипшем счётчике ошибок: /admin_purge_logs all (админ) и перезапуск контейнера по restart_requested.json;
  • паспорт создаётся при старте, если не задан только env JSON.

Запуск из корня репозитория:

    python scripts/full_system_check.py
    python scripts/full_system_check.py --no-pytest   # только структура и конфиг
    python scripts/full_system_check.py --connectivity-only   # только сеть + ключи (20 с)
    python scripts/check_connectivity.py   # то же, JSON + строки

Чек-лист «бот отвечает живо» (эксплуатация):
  • OPENROUTER_API_KEY и рабочая модель OPENROUTER_MODEL_FREE (бесплатные модели часто дают пустой ответ).
  • В группе бот видит только /команды, ответ на бота или @mention — иначе молчит (так задумано).
  • RESILIENCE + safe mode: узкий SAFE_MODE_MODULE_ALLOWLIST; сброс журнала /admin_purge_logs all при залипшем error_total.
  • Антифлуд: при лимите теперь хотя бы редкая подсказка «подождите»; при необходимости ослабить MAX_MSG_PER_10S в .env.
  • Обычный диалог маршрутизируется в chat-orchestrator раньше smartchat (один стабильный путь к LLM).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except Exception:
        pass


def check_env_hints() -> bool:
    """Критичные ключи для реального запуска бота — только подсказки."""
    print("\n=== Переменные окружения (подсказки) ===")
    ok = True
    token = (os.getenv("TELEGRAM_TOKEN") or "").strip()
    if not token:
        print("[WARN] TELEGRAM_TOKEN пуст — бот не запустится (для CI это нормально)")
    else:
        print("[OK] TELEGRAM_TOKEN задан")
    if not (os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip():
        print("[WARN] Нет OPENROUTER_API_KEY / OPENAI_API_KEY — LLM может быть недоступен")
    return ok


def check_app_config() -> bool:
    print("\n=== AppConfig.validate() ===")
    from core.config_manager import get_config

    r = get_config().validate()
    errs = r.get("errors") or []
    warns = r.get("warnings") or []
    for e in errs:
        print(f"[ERROR] {e}")
    for w in warns:
        print(f"[WARN] {w}")
    if r.get("ok"):
        print("[OK] Конфигурация валидна")
        return True
    print("[ERROR] Ошибки конфигурации")
    return False


def run_verify_structure() -> bool:
    print("\n=== verify_structure ===")
    from verify_structure import check_core_files, check_modules

    a = check_core_files()
    b = check_modules()
    return a and b


def run_pytest() -> bool:
    print("\n=== pytest tests/ ===")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=line"],
        cwd=str(ROOT),
    )
    if r.returncode == 0:
        print("[OK] pytest")
        return True
    print("[ERROR] pytest завершился с кодом", r.returncode)
    return False


def discover_tests_py(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return sorted(base.glob("*/tests.py"))


def run_plugin_unittests() -> bool:
    print("\n=== unittest плагинов (modules/*/tests.py, core_libraries/*/tests.py) ===")
    files = discover_tests_py(ROOT / "modules") + discover_tests_py(ROOT / "core_libraries")
    if not files:
        print("[SKIP] Нет tests.py в модулях")
        return True
    all_ok = True
    for fp in files:
        rel = fp.relative_to(ROOT).with_suffix("")
        mod = ".".join(rel.parts)
        print(f"--- {mod} ---")
        r = subprocess.run([sys.executable, "-m", "unittest", mod], cwd=str(ROOT))
        if r.returncode != 0:
            all_ok = False
    if all_ok:
        print("[OK] Все unittest из плагинов прошли")
    else:
        print("[ERROR] Часть unittest плагинов упала")
    return all_ok


def run_connectivity_online() -> bool:
    """Реальные HTTP-запросы: Telegram getMe + OpenRouter (CONNECTIVITY_CHECK_TIMEOUT_SEC, по умолчанию 20)."""
    import asyncio
    import json as json_lib

    from core.connectivity_check import run_connectivity_checks

    print("\n=== Сеть и ключи (онлайн, таймаут из CONNECTIVITY_CHECK_TIMEOUT_SEC) ===")
    report = asyncio.run(run_connectivity_checks())
    print(json_lib.dumps(report, ensure_ascii=False, indent=2))
    for ln in report.get("lines") or []:
        if ln:
            print("—", ln)
    if report.get("ok"):
        print("[OK] Telegram и OpenRouter ответили.")
        return True
    print("[ERROR] Проверка сети/ключей не пройдена — см. поля telegram/openrouter.")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Полная проверка gemma_bot")
    parser.add_argument("--no-pytest", action="store_true", help="Пропустить pytest и unittest плагинов")
    parser.add_argument(
        "--connectivity",
        action="store_true",
        help="В конце выполнить онлайн-проверку Telegram + OpenRouter (таймаут 20 с по умолчанию)",
    )
    parser.add_argument(
        "--connectivity-only",
        action="store_true",
        help="Только онлайн-проверка сети и ключей (без pytest)",
    )
    args = parser.parse_args()

    _load_dotenv()
    print(f"Root: {ROOT}\n")

    if args.connectivity_only:
        ok = run_connectivity_online()
        return 0 if ok else 2

    check_env_hints()
    cfg_ok = check_app_config()
    struct_ok = run_verify_structure()

    if not cfg_ok or not struct_ok:
        print("\n[ERROR] Базовые проверки не пройдены.")
        return 1

    if args.no_pytest:
        print("\n[OK] Проверка без тестов завершена.")
        if args.connectivity:
            return 0 if run_connectivity_online() else 2
        return 0

    py_ok = run_pytest()
    plug_ok = run_plugin_unittests()
    if not (py_ok and plug_ok):
        return 1
    print("\n[OK] Полная проверка пройдена.")
    if args.connectivity:
        return 0 if run_connectivity_online() else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
