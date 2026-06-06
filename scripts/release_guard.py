#!/usr/bin/env python3
"""
Pre-release guard — fail-fast проверки перед выкатом.

Гейтит выкат по трём уровням:
  smoke           — самые быстрые инварианты (каталог команд, контракты плагинов,
                    компиляция main/api, docs_wiki_lint). Должны зелёные ВСЕГДА перед каждым commit.
  anti-regression — короткий регресс-набор, ловящий уже встречавшиеся проблемы
                    (см. константу ANTI_REGRESSION_TESTS в этом файле).
  full            — full pytest + unittest плагинов (как scripts/full_system_check.py).

Запуск:
    python scripts/release_guard.py                    # smoke + anti-regression
    python scripts/release_guard.py --smoke            # только smoke
    python scripts/release_guard.py --full             # + полный pytest

Возврат:
    0 — все гейты зелёные.
    1 — провал гейта (см. вывод).
"""
from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows-консоль (cp1251) падает на эмодзи из манифестов плагинов.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

ANTI_REGRESSION_TESTS: List[str] = [
    "tests/test_cursor_guard_hooks.py",
    "tests/test_command_catalog.py",
    "tests/test_pending_flow.py",
    "tests/test_answer_quality.py",
    "tests/test_plugin_contract.py",
    "tests/test_modules_catalog.py",
    "tests/test_memory_plugin_module.py",
    "tests/test_memory_slash_bridge.py",
    "tests/test_schedule_storage_migration.py",
    "tests/test_stable_pdf_parser.py",
    "tests/test_tier_b_modules_smoke.py",
    "tests/test_sync_plugin_denylist.py",
    "tests/test_plugin_admin_ops.py",
    "tests/test_slash_exclusive.py",
    "tests/test_inline_slash_dispatch.py",
    "tests/test_local_ops_modules.py",
    "tests/test_orchestrator_plugin_command_routing.py",
    "tests/test_user_image_pending.py",
    "tests/test_admin_bug_report.py",
    "tests/test_acc11_honest_refusal.py",
    "tests/test_agent_test_runner_dialog_turns.py",
    "tests/test_analyze_brain_recent_ab.py",
    "tests/test_policy_memory_research.py",
    "tests/test_agent_reliability_horizon.py",
    "tests/test_dialogue_slot_memory_hints.py",
    "tests/test_policy_memory_runtime.py",
    "tests/test_reply_mode_footer.py",
    "tests/test_analyze_stage_ms.py",
    "tests/test_autopilot_mode.py",
    "tests/test_llm_task_outline.py",
    "tests/test_pipeline_chat_lock.py",
    "tests/test_profile_route_guard.py",
    "tests/test_incident_route_regression.py",
    "tests/test_user_correction_bus.py",
    "tests/test_heavy_response_reflection.py",
    "tests/test_product_behavior.py",
    "tests/test_turn_quality_loop.py",
    "tests/test_user_facing_contract.py",
    "tests/test_context_tool_trim.py",
    "tests/test_llm_telemetry_kind.py",
    "tests/test_kv_profile_sticky.py",
    "tests/test_ops_trace.py",
    "tests/test_async_spawn.py",
    "tests/test_brain_self_verify_pass.py",
    "tests/test_auto_reasoning_stage.py",
    "tests/test_pipeline_routing.py",
    "tests/test_pipeline_session_prep.py",
    "tests/test_pipeline_early_guards.py",
    "tests/test_pipeline_first_stage.py",
    "tests/test_user_facts_pet.py",
    "tests/test_memory_prompt_tiers.py",
    "tests/test_telegram_inbound_dedup.py",
    "tests/test_facts_confirm_lane.py",
    "tests/test_geo_nearby_reply.py",
    "tests/test_heuristic_false_positives.py",
    "tests/test_heuristic_context_gate.py",
    "tests/test_memory_regression.py",
    "tests/test_memory_ops_report.py",
    "tests/test_turn_observer_gate_audit.py",
    "tests/test_heuristic_misses_log.py",
    "tests/test_heuristic_w3_b5.py",
    "tests/test_news_reply.py",
    "tests/test_weather_reply.py",
    "tests/test_conversation_epoch.py",
    "tests/test_lexical_dialog_recall.py",
    "tests/test_session_digest_dedup.py",
    "tests/test_telegram_nav.py",
    "tests/test_situation_playbook.py",
    "tests/test_code_empty_recovery.py",
    "tests/test_golden_promote_and_telemetry.py",
    "tests/test_reform_probe_support.py",
    "tests/test_architecture_turn_resolver_guard.py",
    "tests/test_dialogue_recheck_anchor.py",
    "tests/test_wall_clock_intent.py",
    "tests/test_classify_turn_outcome.py",
    "tests/test_text_leak_scan.py",
    "tests/test_timezone_inference.py",
    "tests/test_audit_p0_fixes.py",
    "tests/test_pre_llm_plan.py",
    "tests/test_pre_llm_intent.py",
    "tests/test_admin_ops_metrics.py",
    "tests/test_admin_ops_notify.py",
    "tests/test_product_finish_close.py",
    "tests/test_article_thread_followup.py",
    "tests/test_tool_execution_report_leak.py",
    "tests/test_user_facts_paste_article.py",
    "tests/test_reminder_cancel_nl.py",
    "tests/test_facts_idle_ack.py",
    "tests/test_brain_helpers.py",
    "tests/test_reg_chain_acc_corpus.py",
]


def _print_header(title: str) -> None:
    print(f"\n=== {title} ===")


def gate_test_quality_lint() -> bool:
    _print_header("smoke: test_quality_lint")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "test_quality_lint.py")],
        cwd=str(ROOT),
    )
    if r.returncode != 0:
        print("[ERROR] test_quality_lint — слабые assert в tests/")
        return False
    print("[OK] test_quality_lint")
    return True


def gate_env_inventory() -> bool:
    _print_header("smoke: env inventory (duplicates)")
    r = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "env_inventory_audit.py"),
            "--env",
            str(ROOT / ".env.example"),
            "--fail-on-duplicates",
        ],
        cwd=str(ROOT),
    )
    if r.returncode != 0:
        print("[ERROR] env_inventory_audit — дубликаты KEY= в .env.example")
        return False
    print("[OK] env_inventory_audit")
    return True


def gate_cursor_rules_health() -> bool:
    _print_header("smoke: cursor rules health")
    script = ROOT / "scripts" / "check_cursor_rules_health.py"
    if not script.is_file():
        print("[SKIP] check_cursor_rules_health (public build)")
        return True
    r = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
    )
    if r.returncode != 0:
        print("[ERROR] check_cursor_rules_health")
        return False
    print("[OK] check_cursor_rules_health")
    return True


def gate_docs_wiki_lint() -> bool:
    _print_header("smoke: docs_wiki_lint")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "docs_wiki_lint.py")],
        cwd=str(ROOT),
    )
    if r.returncode != 0:
        print("[ERROR] docs_wiki_lint")
        return False
    print("[OK] docs_wiki_lint")
    return True


def gate_smoke_imports() -> bool:
    _print_header("smoke: компиляция main/api")
    ok = True
    for entry in ("main.py", "api.py"):
        p = ROOT / entry
        if not p.is_file():
            print(f"[SKIP] {entry} отсутствует")
            continue
        r = subprocess.run([sys.executable, "-m", "py_compile", str(p)], cwd=str(ROOT))
        if r.returncode != 0:
            print(f"[ERROR] py_compile failed для {entry}")
            ok = False
        else:
            print(f"[OK] {entry}")
    return ok


def gate_command_catalog() -> bool:
    _print_header("smoke: каталог команд")
    try:
        importlib.invalidate_caches()
        catalog = importlib.import_module("core.command_catalog")
        runners_mod = importlib.import_module("core.input_handlers.telegram_command_runners")
    except Exception as exc:
        print(f"[ERROR] импорт каталога/runners: {exc}")
        return False

    bad: List[str] = []
    for spec in catalog.CORE_COMMANDS:
        if not spec.runner_attr:
            continue
        if not callable(getattr(runners_mod, spec.runner_attr, None)):
            bad.append(f"/{spec.token} -> {spec.runner_attr}")
    if bad:
        print(f"[ERROR] core_commands без runner: {bad}")
        return False
    print(f"[OK] {len(catalog.CORE_COMMANDS)} core-команд, все runner_attr существуют")
    return True


def gate_plugin_contract() -> bool:
    _print_header("smoke: валидация манифестов плагинов")
    try:
        importlib.invalidate_caches()
        prc = importlib.import_module("core.plugin_contract")
        pr = importlib.import_module("core.plugin_registry")
    except Exception as exc:
        print(f"[ERROR] импорт plugin_contract/plugin_registry: {exc}")
        return False

    registry = pr.PluginRegistry(modules_path="./modules")
    registry.load_all_modules()
    snapshot = prc.validate_registry(registry)
    errors = snapshot.get("with_errors", 0)
    warnings = snapshot.get("with_warnings", 0)
    print(
        f"[INFO] плагинов: {snapshot.get('total', 0)},"
        f" с ошибками: {errors}, с предупреждениями: {warnings}"
    )
    for issue in snapshot.get("issues", []):
        sev = issue.get("severity", "?")
        print(
            f"  [{sev:7}] [{issue.get('plugin', '?')}] "
            f"{issue.get('code')}: {issue.get('message')}"
        )
    if errors:
        print("[ERROR] Контракт плагинов содержит ошибки — выкат заблокирован")
        return False
    if warnings:
        print("[WARN] Есть предупреждения, но это не блокирует выкат")
    print("[OK] Контракт плагинов")
    return True


def gate_anti_regression() -> bool:
    _print_header("anti-regression набор")
    existing = [t for t in ANTI_REGRESSION_TESTS if (ROOT / t).is_file()]
    missing = [t for t in ANTI_REGRESSION_TESTS if not (ROOT / t).is_file()]
    for m in missing:
        print(f"[SKIP] {m} (отсутствует)")
    if not existing:
        print("[ERROR] anti-regression набор пуст")
        return False
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=line", *existing]
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        print(f"[ERROR] anti-regression набор упал (rc={r.returncode})")
        return False
    print(f"[OK] anti-regression: {len(existing)} файлов")
    return True


def gate_full_pytest() -> bool:
    _print_header("full pytest")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=line", "tests/"], cwd=str(ROOT)
    )
    if r.returncode != 0:
        print(f"[ERROR] full pytest упал (rc={r.returncode})")
        return False
    print("[OK] full pytest")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-release guard для gemma_bot")
    parser.add_argument("--smoke", action="store_true", help="Только smoke-гейты, без anti-regression")
    parser.add_argument("--full", action="store_true", help="Дополнительно прогнать full pytest")
    args = parser.parse_args()

    print(f"Root: {ROOT}")

    def gate_public_privacy() -> bool:
        _print_header("smoke: privacy (public tree)")
        import os

        cmd = [sys.executable, str(ROOT / "scripts" / "check_public_privacy.py")]
        if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
            cmd.append("--ci")
        r = subprocess.run(cmd, cwd=str(ROOT))
        if r.returncode != 0:
            print("[ERROR] check_public_privacy — утечки в git-tracked файлах")
            return False
        print("[OK] check_public_privacy")
        return True

    smoke_gates = (
        gate_smoke_imports,
        gate_command_catalog,
        gate_plugin_contract,
        gate_public_privacy,
        gate_env_inventory,
        gate_test_quality_lint,
        gate_cursor_rules_health,
        gate_docs_wiki_lint,
    )
    smoke_ok = all(g() for g in smoke_gates)
    if not smoke_ok:
        print("\n[FAIL] smoke gates")
        return 1

    if args.smoke:
        print("\n[OK] smoke-гейты пройдены")
        return 0

    if not gate_anti_regression():
        print("\n[FAIL] anti-regression")
        return 1

    if args.full:
        if not gate_full_pytest():
            print("\n[FAIL] full pytest")
            return 1

    print("\n[OK] release guard зелёный")
    return 0


if __name__ == "__main__":
    sys.exit(main())
