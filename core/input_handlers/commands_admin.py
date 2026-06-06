from __future__ import annotations

import asyncio
import html
import logging
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message

from core.code_cartography import (
    baseline_path,
    build_bundle_slice,
    compare_to_baseline,
    format_code_map_html,
    save_baseline,
    scan_and_maybe_record,
    scan_python_sources,
)
from core.connectivity_check import run_connectivity_checks
from core.admin_bug_runner import run_admin_bug_flow
from core.bug_report_user import bug_report_user_submit_enabled, user_bug_cooldown_ok
from core.admin_zip_copy import copy_admin_zip_to_data_tools
from core.diagnostic_bundle import (
    build_diagnostic_bundle,
    diagnostic_bundle_zip_bytes,
)
from core.data_governance import DG
from core.development_passport import save_passport_patch
from core.recovery_autonomy import build_unified_health_snapshot, resolve_bundle_id
from core.telegram_ui import (
    code_block_html,
    esc,
    format_action_result_html,
    format_admin_logs_header_html,
    format_admin_user_facts_html,
    format_auto_idea_html,
    format_auto_review_html,
    format_auto_suggestions_html,
    format_backup_list_html,
    format_development_passport_block_html,
    format_governance_html,
    format_health_short_html,
    format_anti_flood_html,
    format_operator_panel_html,
    format_purge_result_html,
    format_pulse_html,
    format_usage_digest_html,
    format_xray_html,
    format_resilience_detail_html,
    format_unified_health_html,
    format_llm_usage_html,
    format_efficiency_html,
    format_plugin_health_html,
    format_reasoning_quality_html,
)
from core.plugin_admin_ops import is_generated_plugin_name, normalize_plugin_name, safe_plugin_dir
from core.group_chat_policy import load_group_chat_policy, save_group_chat_policy
from core.telegram_util import reply_code_plain_chunks, reply_html_chunks, reply_json_chunks, sanitize_html
from core.input_handlers.admin_access import admin_guard as _admin_guard
from core.input_handlers.admin_access import effective_admin_user_id
from core.input_handlers.telegram_command_runners import (
    run_clear_all_patches,
    run_export_patches,
    run_forget_patch,
    run_list_patches,
    run_pending_suggested_patch,
    run_remember_patch,
)
from core.ephemeral_autolearn import pending_approve, pending_dismiss
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)


def register(layer: Any) -> None:
    dp = layer.dp

    @dp.message(Command("admin", ignore_mention=True))
    async def handle_admin(message: Message):
        if not await _admin_guard(message, layer):
            return
        await message.answer(
            sanitize_html(layer._admin_module.menu_intro_html()),
            parse_mode="HTML",
            reply_markup=layer._admin_module.menu_keyboard(page=1),
        )

    @dp.message(Command("admin_stats", ignore_mention=True))
    async def handle_admin_stats(message: Message):
        if not await _admin_guard(message, layer):
            return
        await reply_html_chunks(message, layer._admin_module.stats_summary_html())

    @dp.message(Command("admin_stats_json", ignore_mention=True))
    async def handle_admin_stats_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        await reply_json_chunks(message, layer._admin_module.stats(), ensure_ascii=False, indent=2)

    def _admin_llm_usage_parse_args(args: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for part in (args or "").split():
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            k, v = k.strip().lower(), v.strip()
            if k == "days":
                try:
                    out["days"] = float(v)
                except ValueError:
                    pass
            elif k == "sort" and v in ("date", "cost", "tokens"):
                out["sort"] = v
            elif k == "limit":
                try:
                    out["limit"] = int(v)
                except ValueError:
                    pass
        return out

    @dp.message(Command("admin_llm_usage", ignore_mention=True))
    async def handle_admin_llm_usage(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.llm_usage_store import aggregate_usage, recent_rows, sorted_records
        from core.monitoring import MONITOR

        opts = _admin_llm_usage_parse_args(command.args or "")
        days = float(opts.get("days", 30.0))
        days = max(1.0, min(days, 365.0))
        sort = str(opts.get("sort", "date"))
        limit = int(opts.get("limit", 25))
        agg = aggregate_usage(days=days)
        window_rows = recent_rows(days=days)
        top = sorted_records(window_rows, sort=sort, limit=limit)
        nanos = int(MONITOR.counters.get("openrouter_cost_credits_nanos_total", 0))
        session_usd = nanos / 1e9
        html_out = format_llm_usage_html(
            agg,
            session_cost_usd=session_usd,
            top_rows=top,
            sort_label=sort,
        )
        await reply_html_chunks(message, html_out)

    @dp.message(Command("admin_llm_usage_json", ignore_mention=True))
    async def handle_admin_llm_usage_json(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.llm_usage_store import aggregate_usage, recent_rows, sorted_records

        opts = _admin_llm_usage_parse_args(command.args or "")
        days = float(opts.get("days", 30.0))
        days = max(1.0, min(days, 365.0))
        sort = str(opts.get("sort", "date"))
        limit = int(opts.get("limit", 80))
        agg = aggregate_usage(days=days)
        window_rows = recent_rows(days=days)
        top = sorted_records(window_rows, sort=sort, limit=limit)
        await reply_json_chunks(
            message,
            {"aggregate": agg, "recent_sorted": top},
            ensure_ascii=False,
            indent=2,
        )

    @dp.message(Command("admin_llm_usage_reset", ignore_mention=True))
    async def handle_admin_llm_usage_reset(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.llm_usage_store import reset_records

        args = (command.args or "").strip().lower()
        if args != "confirm":
            await message.answer(
                sanitize_html(
                    "Для защиты от случайного сброса используйте: "
                    "<code>/admin_llm_usage_reset confirm</code>"
                ),
                parse_mode="HTML",
            )
            return
        rep = reset_records()
        if rep.get("ok"):
            await message.answer(
                sanitize_html(
                    "Журнал LLM usage очищен.\n"
                    f"<code>{esc(str(rep.get('log_path') or ''))}</code>\n"
                    "<i>MONITOR runtime-счётчики не сброшены.</i>"
                ),
                parse_mode="HTML",
            )
            return
        await message.answer(
            sanitize_html(
                "Не удалось очистить журнал:\n"
                f"<code>{esc(str(rep.get('error') or 'unknown_error'))}</code>"
            ),
            parse_mode="HTML",
        )

    @dp.message(Command("admin_kv_debug", ignore_mention=True))
    async def handle_admin_kv_debug(message: Message):
        if not await _admin_guard(message, layer):
            return
        uid = str(message.from_user.id) if message.from_user else ""
        gid: str | None = None
        if message.chat and message.chat.type in ("group", "supergroup"):
            gid = str(message.chat.id)
        payload = layer._admin_module.kv_debug_snapshot(user_id=uid, group_id=gid)
        sess = payload.get("session") if isinstance(payload.get("session"), dict) else {}
        latest = payload.get("latest") if isinstance(payload.get("latest"), dict) else {}
        window = payload.get("session_window") if isinstance(payload.get("session_window"), dict) else {}
        rolling = payload.get("rolling_window") if isinstance(payload.get("rolling_window"), dict) else {}
        hit_rate_pct = float(window.get("hit_rate") or 0.0) * 100.0
        rolling_hit_pct = float(rolling.get("hit_rate") or 0.0) * 100.0
        rolling_cov_pct = float(rolling.get("cache_coverage") or 0.0) * 100.0
        body = (
            "<b>KV Debug</b>\n\n"
            f"session_id: <code>{esc(str(sess.get('session_id') or ''))}</code>\n"
            f"profile: <b>{esc(str(sess.get('profile') or '-'))}</b>\n"
            f"active_bucket: <b>{esc(str(sess.get('active_bucket') or 'main'))}</b>\n"
            f"pending_bucket: <code>{esc(str(sess.get('pending_bucket') or '-'))}</code> "
            f"(n={esc(str(sess.get('pending_bucket_count') or 0))})\n"
            f"cached_tok: <b>{esc(str(latest.get('cached_tok') or 0))}</b>\n"
            f"prompt_tokens: <b>{esc(str(latest.get('prompt_tokens') or 0))}</b>\n"
            f"reuse_hits: <b>{esc(str(payload.get('reuse_hits') or 0))}</b>\n"
            f"reuse_misses: <b>{esc(str(payload.get('reuse_misses') or 0))}</b>\n"
            f"rows_for_session: <b>{esc(str(payload.get('rows_for_session') or 0))}</b>\n"
            f"session_hits: <b>{esc(str(window.get('hits') or 0))}</b>\n"
            f"session_misses: <b>{esc(str(window.get('misses') or 0))}</b>\n"
            f"session_hit_rate: <b>{esc(f'{hit_rate_pct:.1f}%')}</b>\n"
            f"rolling_rows: <b>{esc(str(rolling.get('rows') or 0))}</b> (limit={esc(str(rolling.get('rows_limit') or 0))})\n"
            f"rolling_cached_sum: <b>{esc(str(rolling.get('cached_tokens_sum') or 0))}</b>\n"
            f"rolling_prompt_sum: <b>{esc(str(rolling.get('prompt_tokens_sum') or 0))}</b>\n"
            f"rolling_cache_coverage: <b>{esc(f'{rolling_cov_pct:.1f}%')}</b>\n"
            f"rolling_hit_rate: <b>{esc(f'{rolling_hit_pct:.1f}%')}</b>\n"
            f"last_reset_reason: <code>{esc(str(sess.get('last_reset_reason') or '-'))}</code>"
        )
        await reply_html_chunks(message, body)

    @dp.message(Command("admin_kv_debug_json", ignore_mention=True))
    async def handle_admin_kv_debug_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        uid = str(message.from_user.id) if message.from_user else ""
        gid: str | None = None
        if message.chat and message.chat.type in ("group", "supergroup"):
            gid = str(message.chat.id)
        payload = layer._admin_module.kv_debug_snapshot(user_id=uid, group_id=gid)
        await reply_json_chunks(message, payload, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_router", ignore_mention=True))
    async def handle_admin_router(message: Message):
        if not await _admin_guard(message, layer):
            return
        text = message.text or ""
        parts = text.strip().split(None, 1)
        if len(parts) > 1 and parts[1].strip().lower() == "reset":
            layer._admin_module.router_reset()
            await message.reply("Router cache and metrics reset.")
            return
        snap = layer._admin_module.router_status_snapshot()
        m = snap.get("metrics") or {}
        sources = m.get("sources") or {}
        profiles = m.get("profiles") or {}
        lines = [
            "<b>Router Classifier Status</b>\n",
            f"LRU cache: <b>{snap.get('lru_size', 0)}</b> / 1024",
            f"Permanent: <b>{snap.get('permanent_size', 0)}</b>",
            f"Raw log: <b>{snap.get('raw_log_size', 0)}</b> lines",
            f"Samples in window: <b>{m.get('samples', 0)}</b>\n",
            "Sources:",
        ]
        for src, cnt in sorted(sources.items(), key=lambda x: -x[1]):
            lines.append(f"  {src}: {cnt}")
        lines.append("")
        lat_med = m.get("latency_ms_median", 0)
        lat_p95 = m.get("latency_ms_p95", 0)
        conf_med = m.get("confidence_median", 0)
        lines.append(f"Latency median: <b>{lat_med:.0f}</b> ms")
        lines.append(f"Latency p95: <b>{lat_p95:.0f}</b> ms")
        lines.append(f"Confidence median: <b>{conf_med:.2f}</b>")
        lines.append(f"\nProfiles in window:")
        for prof, cnt in sorted(profiles.items(), key=lambda x: -x[1]):
            lines.append(f"  <b>{prof}</b>: {cnt}")
        lines.append("")
        lines.append("<code>/admin_router reset</code> — сброс")
        await reply_html_chunks(message, "\n".join(lines))

    @dp.message(Command("admin_pulse", ignore_mention=True))
    async def handle_admin_pulse(message: Message):
        if not await _admin_guard(message, layer):
            return
        snap = layer._admin_module.live_pulse_snapshot()
        await reply_html_chunks(message, format_pulse_html(snap))

    @dp.message(Command("admin_pulse_json", ignore_mention=True))
    async def handle_admin_pulse_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        await reply_json_chunks(message, layer._admin_module.live_pulse_snapshot(), ensure_ascii=False, indent=2)

    @dp.message(Command("diag", ignore_mention=True))
    @dp.message(Command("admin_diag", ignore_mention=True))
    async def handle_owner_diag(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.owner_diag import collect_owner_diag, format_owner_diag_html

        st = collect_owner_diag()
        await reply_html_chunks(message, format_owner_diag_html(st))

    @dp.message(Command("session_trim", ignore_mention=True))
    @dp.message(Command("admin_session_trim", ignore_mention=True))
    async def handle_session_trim(message: Message, command: CommandObject):
        """Обрезка recent_messages / summary (факты не трогаем)."""
        if not await _admin_guard(message, layer):
            return
        from core.input_handlers.telegram_command_runners import _profile_group_id
        from core.session_trim import format_trim_report_html, trim_user_session

        uid = str(message.from_user.id)
        gid = _profile_group_id(message)
        args = (command.args or "").strip().split()
        keep: Optional[int] = None
        bump_kv = False
        clear_slots = False
        for a in args:
            low = a.lower()
            if low in ("kv", "--kv", "reset_kv"):
                bump_kv = True
            elif low in ("slots", "--slots"):
                clear_slots = True
            elif a.isdigit():
                keep = int(a)
        rep = trim_user_session(
            uid,
            gid,
            keep_recent=keep,
            clear_dialogue_slots=clear_slots,
            bump_kv=bump_kv,
        )
        await reply_html_chunks(message, format_trim_report_html(rep))

    @dp.message(Command("admin_xray", ignore_mention=True))
    async def handle_admin_xray(message: Message):
        if not await _admin_guard(message, layer):
            return
        snap = layer._admin_module.xray_snapshot()
        await reply_html_chunks(message, format_xray_html(snap))

    @dp.message(Command("admin_xray_json", ignore_mention=True))
    async def handle_admin_xray_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        await reply_json_chunks(message, layer._admin_module.xray_snapshot(), ensure_ascii=False, indent=2)

    @dp.message(Command("admin_memory_insight", ignore_mention=True))
    async def handle_admin_memory_insight(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.input_handlers.admin_slash_dispatch import _memory_insight_scope
        from core.memory_runtime_report import build_memory_insight_payload, format_memory_insight_html

        n, uid, gid = _memory_insight_scope(message, command.args or "")
        payload = build_memory_insight_payload(limit_per_file=n, user_id=uid, group_id=gid)
        await reply_html_chunks(message, format_memory_insight_html(payload))

    @dp.message(Command("admin_memory_insight_json", ignore_mention=True))
    async def handle_admin_memory_insight_json(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.input_handlers.admin_slash_dispatch import _memory_insight_scope
        from core.memory_runtime_report import build_memory_insight_payload

        n, uid, gid = _memory_insight_scope(message, command.args or "")
        payload = build_memory_insight_payload(limit_per_file=n, user_id=uid, group_id=gid)
        await reply_json_chunks(message, payload, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_memory_ops", ignore_mention=True))
    async def handle_admin_memory_ops(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.memory_ops_report import build_memory_ops_report

        turns_n = 25
        memory_n = 5
        if command.args:
            parts = (command.args or "").strip().split()
            if parts:
                try:
                    turns_n = max(5, min(int(parts[0]), 50))
                except ValueError:
                    turns_n = 25
            if len(parts) > 1:
                try:
                    memory_n = max(2, min(int(parts[1]), 15))
                except ValueError:
                    memory_n = 5
        uid = str(message.from_user.id) if message.from_user else ""
        text = build_memory_ops_report(
            user_id=uid,
            turns_limit=turns_n,
            memory_limit=memory_n,
        )
        await reply_code_plain_chunks(message, text)

    @dp.message(Command("admin_grim_state", ignore_mention=True))
    async def handle_admin_grim_state(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.admin_kv_state_view import format_grim_state_html, load_grim_state_payload

        uid = str(message.from_user.id) if message.from_user else ""
        if command.args and command.args.strip():
            uid = command.args.strip().split()[0]
        payload = load_grim_state_payload(uid)
        if payload.get("error"):
            await message.answer(str(payload["error"]))
            return
        await reply_html_chunks(message, format_grim_state_html(payload))

    @dp.message(Command("admin_grim_state_json", ignore_mention=True))
    async def handle_admin_grim_state_json(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.admin_kv_state_view import load_grim_state_payload

        uid = str(message.from_user.id) if message.from_user else ""
        if command.args and command.args.strip():
            uid = command.args.strip().split()[0]
        payload = load_grim_state_payload(uid)
        await reply_json_chunks(message, payload, ensure_ascii=False, indent=2)

    def _admin_reputation_parse_args(args: str) -> Dict[str, Any]:
        parts = (args or "").strip().split()
        out: Dict[str, Any] = {}
        for p in parts:
            if p.lower().startswith("branch="):
                out["branch"] = p.split("=", 1)[1].strip()
        if parts and not parts[0].lower().startswith("branch="):
            out["user_id"] = parts[0].strip()
        return out

    @dp.message(Command("admin_reputation", ignore_mention=True))
    async def handle_admin_reputation(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.admin_reputation_view import format_admin_reputation_html, load_admin_reputation_payload

        opts = _admin_reputation_parse_args(command.args or "")
        uid = str(opts.get("user_id") or "").strip() or effective_admin_user_id(message, "")
        br = opts.get("branch")
        payload = load_admin_reputation_payload(uid, branch=br)
        if payload.get("error"):
            await message.answer(
                format_admin_reputation_html(payload),
                parse_mode="HTML",
            )
            return
        await reply_html_chunks(message, format_admin_reputation_html(payload))

    @dp.message(Command("admin_reputation_json", ignore_mention=True))
    async def handle_admin_reputation_json(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.admin_reputation_view import load_admin_reputation_payload

        opts = _admin_reputation_parse_args(command.args or "")
        uid = str(opts.get("user_id") or "").strip() or effective_admin_user_id(message, "")
        br = opts.get("branch")
        payload = load_admin_reputation_payload(uid, branch=br)
        await reply_json_chunks(message, payload, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_reputation_reset", ignore_mention=True))
    async def handle_admin_reputation_reset(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.agent_kv.grim import merge_grim_policy_into
        from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, delete_key, get_json, iter_prefix, set_json
        from core.cdc.engine import build_policy_for_user

        if not agent_kv_enabled():
            await message.answer("AGENT_KV_ENABLED=false")
            return
        raw = (command.args or "").strip()
        if not raw:
            await message.answer(
                sanitize_html(
                    "Usage: /admin_reputation_reset &lt;full_key&gt; [branch]\n"
                    "Маршрут: <code>user|module|intent</code> — сброс reputation + cdc_agg.\n"
                    "Скилл: <code>user|skill_name</code> — сброс reputation_skill + cdc_agg_skill.\n"
                    "Пример: <code>/admin_reputation_reset 123456789|__fallback__|command</code>\n"
                    "После сброса маршрута пересобирается <code>cdc_policy</code> (с учётом grim)."
                ),
                parse_mode="HTML",
            )
            return
        parts = raw.split()
        key = parts[0].strip()
        br = parts[1].strip() if len(parts) >= 2 else agent_kv_branch()
        parts = key.split("|")
        if len(parts) < 2:
            await message.answer("Ключ: user|module|intent или user|skill_name.")
            return
        uid = parts[0].strip()
        if not uid:
            await message.answer("Пустой user_id в ключе.")
            return
        is_skill = len(parts) == 2
        if is_skill:
            delete_key("reputation_skill", key, branch=br)
            delete_key("cdc_agg_skill", key, branch=br)
            await message.answer(
                sanitize_html(
                    f"OK: сброшены reputation_skill + cdc_agg_skill.\n"
                    f"Ключ: <code>{esc(key)}</code>\nbranch: <code>{esc(br)}</code>"
                ),
                parse_mode="HTML",
            )
            return
        delete_key("reputation", key, branch=br)
        delete_key("cdc_agg", key, branch=br)
        partial = {k: v for k, v in iter_prefix("cdc_agg", f"{uid}|", branch=br)}
        policy = build_policy_for_user(uid, partial)
        grim = get_json("grim", uid, branch=br)
        merged = merge_grim_policy_into(policy, grim)
        set_json("cdc_policy", uid, merged, branch=br, priority=60)
        await message.answer(
            sanitize_html(
                f"OK: сброшены reputation + cdc_agg, обновлён cdc_policy для user <code>{esc(uid)}</code>.\n"
                f"Ключ: <code>{esc(key)}</code>\nbranch: <code>{esc(br)}</code>"
            ),
            parse_mode="HTML",
        )

    def _load_self_model_payload(uid: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"user_id": uid, "self_model": {}}
        try:
            from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, get_json

            if agent_kv_enabled():
                payload["branch"] = agent_kv_branch()
                payload["self_model"] = get_json("self_model", uid, branch=agent_kv_branch()) or {}
        except Exception as e:
            logger.debug("%s optional failed: %s", 'commands_admin', e, exc_info=True)
        if not payload.get("self_model") and getattr(layer, "orchestrator", None) is not None:
            try:
                rec = layer.orchestrator.behavior_store.load(uid, None)
                if isinstance(rec.get("self_model"), dict):
                    payload["self_model"] = rec.get("self_model") or {}
            except Exception as e:
                logger.debug("%s optional failed: %s", 'commands_admin', e, exc_info=True)
        return payload

    @dp.message(Command("admin_self_model", ignore_mention=True))
    async def handle_admin_self_model(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.admin_kv_state_view import format_self_model_html

        uid = str(message.from_user.id) if message.from_user else ""
        if command.args and command.args.strip():
            uid = command.args.strip().split()[0]
        await reply_html_chunks(message, format_self_model_html(_load_self_model_payload(uid)))

    @dp.message(Command("admin_self_model_json", ignore_mention=True))
    async def handle_admin_self_model_json(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        uid = str(message.from_user.id) if message.from_user else ""
        if command.args and command.args.strip():
            uid = command.args.strip().split()[0]
        await reply_json_chunks(message, _load_self_model_payload(uid), ensure_ascii=False, indent=2)

    def _load_session_task_payload(uid: str, gid: Optional[str]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"user_id": uid, "group_id": gid, "session_task": {}}
        try:
            if getattr(layer, "orchestrator", None) is not None:
                rec = layer.orchestrator.behavior_store.load(uid, gid)
                if isinstance(rec.get("session_task"), dict):
                    payload["session_task"] = rec.get("session_task") or {}
        except Exception as e:
            logger.debug("%s optional failed: %s", 'commands_admin', e, exc_info=True)
        return payload

    @dp.message(Command("admin_session_task", ignore_mention=True))
    async def handle_admin_session_task(message: Message, command: CommandObject):
        """Последний маршрут + вызов инструмента мозга (session_task в behavior JSON)."""
        if not await _admin_guard(message, layer):
            return
        from core.admin_kv_state_view import format_session_task_html

        uid = str(message.from_user.id) if message.from_user else ""
        gid: Optional[str] = None
        if command.args and command.args.strip():
            parts = command.args.strip().split()
            uid = parts[0]
            if len(parts) >= 2:
                gid = parts[1]
        await reply_html_chunks(message, format_session_task_html(_load_session_task_payload(uid, gid)))

    @dp.message(Command("admin_session_task_json", ignore_mention=True))
    async def handle_admin_session_task_json(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        uid = str(message.from_user.id) if message.from_user else ""
        gid: Optional[str] = None
        if command.args and command.args.strip():
            parts = command.args.strip().split()
            uid = parts[0]
            if len(parts) >= 2:
                gid = parts[1]
        await reply_json_chunks(message, _load_session_task_payload(uid, gid), ensure_ascii=False, indent=2)

    @dp.message(Command("admin_kv_rollback", ignore_mention=True))
    async def handle_admin_kv_rollback(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.agent_kv.store import agent_kv_enabled, rollback_to_version

        if not agent_kv_enabled():
            await message.answer("AGENT_KV_ENABLED=false")
            return
        parts = (command.args or "").strip().split()
        if len(parts) < 3:
            await message.answer("Usage: /admin_kv_rollback <namespace> <key> <version> [branch]")
            return
        ns, key, ver_s = parts[0], parts[1], parts[2]
        br = parts[3] if len(parts) >= 4 else None
        try:
            ver = int(ver_s)
        except ValueError:
            await message.answer("version must be int")
            return
        ok = rollback_to_version(ns, key, ver, branch=br)
        await message.answer("OK" if ok else "NOT_FOUND")

    @dp.message(Command("admin_kv_copy_branch", ignore_mention=True))
    async def handle_admin_kv_copy_branch(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.agent_kv.store import agent_kv_enabled, copy_branch

        if not agent_kv_enabled():
            await message.answer("AGENT_KV_ENABLED=false")
            return
        parts = (command.args or "").strip().split()
        if len(parts) != 2:
            await message.answer("Usage: /admin_kv_copy_branch <from_branch> <to_branch>")
            return
        copy_branch(parts[0], parts[1])
        await message.answer("OK")

    @dp.message(Command("admin_kv_branches", ignore_mention=True))
    async def handle_admin_kv_branches(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.admin_kv_state_view import format_kv_branches_html
        from core.agent_kv.store import agent_kv_enabled, list_branches

        if not agent_kv_enabled():
            await message.answer("AGENT_KV_ENABLED=false")
            return
        await reply_html_chunks(message, format_kv_branches_html({"branches": list_branches()}))

    @dp.message(Command("admin_kv_branches_json", ignore_mention=True))
    async def handle_admin_kv_branches_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.agent_kv.store import agent_kv_enabled, list_branches

        if not agent_kv_enabled():
            await message.answer("AGENT_KV_ENABLED=false")
            return
        await reply_json_chunks(message, {"branches": list_branches()}, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_usage_digest", ignore_mention=True))
    async def handle_admin_usage_digest(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.usage_learning import build_digest_payload, digest_slot_utc

        payload = build_digest_payload(
            slot_label=digest_slot_utc(),
            orchestrator=layer.orchestrator,
        )
        await reply_html_chunks(message, format_usage_digest_html(payload))

    @dp.message(Command("admin_usage_digest_json", ignore_mention=True))
    async def handle_admin_usage_digest_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.usage_learning import build_digest_payload, digest_slot_utc

        payload = build_digest_payload(
            slot_label=digest_slot_utc(),
            orchestrator=layer.orchestrator,
        )
        await reply_json_chunks(message, payload, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_self", ignore_mention=True))
    async def handle_admin_self(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.admin_self_status import build_admin_self_html

        await reply_html_chunks(message, build_admin_self_html())

    @dp.message(Command("admin_turns", ignore_mention=True))
    async def handle_admin_turns(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.turn_observer import format_turns_admin_html, read_recent_turns

        args = (command.args or "").strip().lower()
        issues_only = "issues" in args.split()
        limit = 20
        for tok in (command.args or "").split():
            if tok.isdigit():
                limit = max(5, min(100, int(tok)))
                break
        rows = read_recent_turns(limit=limit, issues_only=issues_only)
        title = "Ходы с issues" if issues_only else f"Последние {limit} ходов"
        await reply_html_chunks(message, format_turns_admin_html(rows, title=title))

    @dp.message(Command("add_route_example", "add_example", ignore_mention=True))
    async def handle_add_route_example(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.route_example_store import append_route_example, route_examples_path

        raw = (command.args or "").strip()
        if not raw:
            await message.answer(
                "Формат: <code>/add_route_example news_brief Какие новости в мире</code>\n"
                "или <code>/add_route_example --profile weather_brief --text погода в Минске</code>",
                parse_mode="HTML",
            )
            return
        prof = ""
        body = ""
        if raw.startswith("--"):
            parts = raw.split()
            i = 0
            while i < len(parts):
                if parts[i] == "--profile" and i + 1 < len(parts):
                    prof = parts[i + 1]
                    i += 2
                elif parts[i] == "--text" and i + 1 < len(parts):
                    body = " ".join(parts[i + 1 :])
                    break
                else:
                    i += 1
        else:
            bits = raw.split(maxsplit=1)
            prof = bits[0] if bits else ""
            body = bits[1] if len(bits) > 1 else ""
        try:
            rec = append_route_example(
                text=body,
                expected_profile=prof,
                added_by=str(message.from_user.id if message.from_user else ""),
            )
        except ValueError as e:
            await message.answer(f"Не добавлено: <code>{esc(str(e))}</code>", parse_mode="HTML")
            return
        await message.answer(
            f"✅ route example <code>{esc(str(rec.get('id') or ''))}</code>\n"
            f"profile: <code>{esc(prof)}</code>\n"
            f"path: <code>{esc(str(route_examples_path()))}</code>\n"
            f"<i>Пересобери corpus: python scripts/build_test_corpus.py</i>",
            parse_mode="HTML",
        )

    @dp.message(Command("admin_housekeeping", ignore_mention=True))
    async def handle_admin_housekeeping(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.system_housekeeping import run_housekeeping

        args = (command.args or "").strip().lower()
        dry = not (args in {"run", "apply", "force", "now"})
        rep = run_housekeeping(dry_run=dry)
        st = rep.get("storage_optimization") if isinstance(rep.get("storage_optimization"), dict) else {}
        jc = st.get("jsonl_compaction") if isinstance(st.get("jsonl_compaction"), list) else []
        so = st.get("sqlite_optimization") if isinstance(st.get("sqlite_optimization"), list) else []
        jsonl_trimmed = sum(1 for x in jc if isinstance(x, dict) and x.get("trimmed"))
        sqlite_optimized = sum(1 for x in so if isinstance(x, dict) and x.get("optimized"))
        mode = "dry-run" if dry else "apply"
        txt = (
            f"🧹 housekeeping ({mode})\n"
            f"root: {rep.get('root')}\n"
            f"profile: {rep.get('profile')}\n"
            f"removed_total: {rep.get('removed_total')}\n"
            f"removed_dirs: {len(rep.get('removed_dirs') or [])}\n"
            f"removed_files: {len(rep.get('removed_files') or [])}\n"
            f"scanned_candidates: {rep.get('scanned_candidates')}\n"
            f"jsonl_trimmed: {jsonl_trimmed}\n"
            f"sqlite_optimized: {sqlite_optimized}"
        )
        await reply_code_plain_chunks(message, txt)

    @dp.message(Command("admin_housekeeping_json", ignore_mention=True))
    async def handle_admin_housekeeping_json(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.system_housekeeping import run_housekeeping

        args = (command.args or "").strip().lower()
        dry = not (args in {"run", "apply", "force", "now"})
        rep = run_housekeeping(dry_run=dry)
        await reply_json_chunks(message, rep, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_efficiency", ignore_mention=True))
    async def handle_admin_efficiency(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.efficiency_report import build_efficiency_snapshot

        days = 7.0
        if command.args:
            try:
                days = float((command.args or "").strip().split()[0])
            except (ValueError, IndexError):
                days = 7.0
        payload = build_efficiency_snapshot(days=days, orchestrator=layer.orchestrator)
        await reply_html_chunks(message, format_efficiency_html(payload))

    @dp.message(Command("admin_efficiency_json", ignore_mention=True))
    async def handle_admin_efficiency_json(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.efficiency_report import build_efficiency_snapshot

        days = 7.0
        if command.args:
            try:
                days = float((command.args or "").strip().split()[0])
            except (ValueError, IndexError):
                days = 7.0
        payload = build_efficiency_snapshot(days=days, orchestrator=layer.orchestrator)
        await reply_json_chunks(message, payload, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_plugins_health", ignore_mention=True))
    async def handle_admin_plugins_health(message: Message):
        if not await _admin_guard(message, layer):
            return
        payload = layer._admin_module.plugin_health_snapshot()
        await reply_html_chunks(message, format_plugin_health_html(payload))

    @dp.message(Command("admin_plugins_health_json", ignore_mention=True))
    async def handle_admin_plugins_health_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        payload = layer._admin_module.plugin_health_snapshot()
        await reply_json_chunks(message, payload, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_reasoning_quality", ignore_mention=True))
    async def handle_admin_reasoning_quality(message: Message):
        if not await _admin_guard(message, layer):
            return
        payload = layer._admin_module.reasoning_quality_snapshot()
        await reply_html_chunks(message, format_reasoning_quality_html(payload))

    @dp.message(Command("admin_reasoning_quality_json", ignore_mention=True))
    async def handle_admin_reasoning_quality_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        payload = layer._admin_module.reasoning_quality_snapshot()
        await reply_json_chunks(message, payload, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_access", ignore_mention=True))
    async def handle_admin_access(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.access_gate import admin_access_keyboard, format_admin_panel_html

        await message.answer(
            sanitize_html(format_admin_panel_html()),
            parse_mode="HTML",
            reply_markup=admin_access_keyboard(),
        )

    @dp.message(Command("admin_operator", ignore_mention=True))
    async def handle_admin_operator(message: Message):
        if not await _admin_guard(message, layer):
            return
        await reply_html_chunks(
            message,
            format_operator_panel_html(layer._admin_module.operator_console_snapshot()),
        )

    @dp.message(Command("admin_operator_json", ignore_mention=True))
    async def handle_admin_operator_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        await reply_json_chunks(message, layer._admin_module.operator_console_snapshot(), ensure_ascii=False, indent=2)

    @dp.message(Command("admin_seed_runtime", ignore_mention=True))
    async def handle_admin_seed_runtime(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.runtime_config_seed import (
            format_runtime_seed_report_ru,
            seed_runtime_config_from_examples,
        )

        args = (command.args or "").strip().lower()
        if "all" in args or "full" in args:
            rep = seed_runtime_config_from_examples(force_directive=True, force_operator_rules=True)
        elif "force" in args or "directive" in args:
            rep = seed_runtime_config_from_examples(force_directive=True, force_operator_rules=False)
        else:
            rep = seed_runtime_config_from_examples(force_directive=False, force_operator_rules=False)
        body = (
            "<b>📎 Сиды runtime</b>\n\n"
            + format_runtime_seed_report_ru(rep)
            + "\n\n<i>Без аргументов — только пустые файлы; <code>force</code> — перезаписать директиву; "
            "<code>all</code> — ещё и <code>operator_rules.json</code>.</i>"
        )
        await reply_html_chunks(message, body)

    @dp.message(Command("admin_governance", ignore_mention=True))
    async def handle_admin_governance(message: Message):
        if not await _admin_guard(message, layer):
            return
        await reply_html_chunks(message, format_governance_html(layer._admin_module.governance_status()))

    @dp.message(Command("admin_resilience", ignore_mention=True))
    async def handle_admin_resilience(message: Message):
        if not await _admin_guard(message, layer):
            return
        await reply_html_chunks(message, layer._admin_module.resilience_panel_html())
        rc = getattr(layer.orchestrator, "_resilience", None)
        try:
            payload_snap = rc.snapshot() if rc is not None else {}
            payload_ev = rc.evaluate(layer.orchestrator) if rc is not None else {}
        except Exception as e:
            payload_snap, payload_ev = {}, {"error": str(e)}
        await reply_html_chunks(message, format_resilience_detail_html(payload_snap, payload_ev))

    @dp.message(Command("admin_resilience_json", ignore_mention=True))
    async def handle_admin_resilience_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        rc = getattr(layer.orchestrator, "_resilience", None)
        if rc is None:
            await reply_json_chunks(message, {"error": "resilience unavailable"}, ensure_ascii=False, indent=2)
            return
        payload = {"snapshot": rc.snapshot(), "evaluate": rc.evaluate(layer.orchestrator)}
        await reply_json_chunks(message, payload, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_health", ignore_mention=True))
    async def handle_admin_health(message: Message):
        if not await _admin_guard(message, layer):
            return
        snap = build_unified_health_snapshot(layer.orchestrator)
        hs = layer._admin_module.health_summary()
        await reply_html_chunks(
            message,
            format_health_short_html(hs) + "\n\n" + format_unified_health_html(snap),
        )

    @dp.message(Command("admin_health_json", ignore_mention=True))
    async def handle_admin_health_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        await reply_json_chunks(
            message,
            build_unified_health_snapshot(layer.orchestrator),
            ensure_ascii=False,
            indent=2,
        )

    @dp.message(Command("admin_system", ignore_mention=True))
    async def handle_admin_system(message: Message):
        if not await _admin_guard(message, layer):
            return
        report = layer._admin_module.full_system_report()
        chunks: list[str] = [
            "<b>/admin_system</b> · сводка",
            "<i>Текст по-русски; в <code>/admin_system_json</code> ключи остаются как в коде (англ.).</i>",
            "",
            layer._admin_module.dashboard_html(),
            "",
            layer._admin_module.resilience_panel_html(),
        ]
        uh = report.get("unified_health")
        if isinstance(uh, dict):
            chunks.extend(
                [
                    "",
                    format_unified_health_html(
                        uh,
                        footer_line="",
                    ),
                ]
            )
        chunks.extend(
            [
                "",
                "<i>Список админ-команд по разделам: <code>/help</code> → «Админ» "
                "(кнопки только переключают текст, без запуска слэшей). "
                "Панель: <code>/admin</code> → «Команды».</i>",
            ]
        )
        await reply_html_chunks(message, "\n".join(chunks))

    @dp.message(Command("admin_system_json", ignore_mention=True))
    async def handle_admin_system_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        await reply_json_chunks(
            message,
            layer._admin_module.full_system_report(),
            ensure_ascii=False,
            indent=2,
        )

    @dp.message(Command("admin_diagnostic", ignore_mention=True))
    async def handle_admin_diagnostic(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split(maxsplit=1)
        include_net = False
        if len(parts) > 1:
            flag = parts[1].strip().lower()
            include_net = flag in ("net", "network", "1", "true", "yes", "online")
        try:
            await message.bot.send_chat_action(message.chat.id, "typing")
        except Exception as e:
            logger.debug("%s optional failed: %s", 'commands_admin', e, exc_info=True)
        hint = " (с проверкой сети ~20 с)" if include_net else ""
        await message.answer(
            sanitize_html(f"Собираю диагностический архив{hint}… после готовности пришлю ZIP."),
            parse_mode="HTML",
        )
        try:
            bundle = await build_diagnostic_bundle(
                layer.orchestrator,
                layer._admin_module,
                include_connectivity=include_net,
            )
        except Exception as e:
            await message.answer(f"Не удалось собрать диагностику: {e}")
            return
        zbytes = diagnostic_bundle_zip_bytes(bundle)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fname = f"gemma_diagnostic_{ts}.zip"
        tools_copy = copy_admin_zip_to_data_tools(zbytes, fname)
        cap = (
            "🔬 <b>Диагностика</b>\n"
            f"Сеть в архиве: <code>{'да' if include_net else 'нет'}</code>\n"
            "Внутри: <code>bundle.json</code> + <code>КАК_ЧИТАТЬ_ДИАГНОСТИКУ.txt</code>"
        )
        if tools_copy:
            cap += f"\n\nКопия на сервере: <code>{esc(tools_copy)}</code>\nМожно сразу: <code>/zip_read bundle.json</code>"
        cap = sanitize_html(cap)
        doc = BufferedInputFile(zbytes, filename=fname)
        try:
            await message.answer_document(document=doc, caption=cap, parse_mode="HTML")
        except Exception as e:
            await message.answer(f"Архив собран, но отправка файла не удалась: {e}")

    @dp.message(Command("admin_bug", ignore_mention=True))
    async def handle_admin_bug(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        await run_admin_bug_flow(
            layer,
            message,
            command_args=command.args,
            capture_source="slash_command",
        )

    @dp.message(Command("bug", ignore_mention=True))
    async def handle_user_bug(message: Message, command: CommandObject):
        """Багрепорт от обычного пользователя: ZIP только админам (см. BUG_REPORT_USER_SUBMIT_ENABLED)."""
        if not bug_report_user_submit_enabled():
            await message.answer(
                sanitize_html("Приём отчётов через <code>/bug</code> на этом боте не включён."),
                parse_mode="HTML",
            )
            return
        if str(message.chat.type) != "private":
            await message.answer(
                sanitize_html("Команда <code>/bug</code> доступна только в <b>личных сообщениях</b> с ботом."),
                parse_mode="HTML",
            )
            return
        uid = str(message.from_user.id) if message.from_user else ""
        if layer._admin_module.is_admin(uid):
            await message.answer(
                sanitize_html("Для вас удобнее <code>/admin_bug</code> — архив придёт прямо в этот чат."),
                parse_mode="HTML",
            )
            return
        ok_cd, wait_sec = user_bug_cooldown_ok(uid)
        if not ok_cd:
            await message.answer(
                sanitize_html(f"Следующий отчёт можно отправить примерно через {wait_sec} с."),
                parse_mode="HTML",
            )
            return
        MONITOR.inc("user_bug_slash_total")
        await run_admin_bug_flow(
            layer,
            message,
            command_args=command.args,
            capture_source="user_slash",
            zip_delivery="to_admins_only",
        )

    @dp.message(Command("admin_connectivity", ignore_mention=True))
    async def handle_admin_connectivity(message: Message):
        if not await _admin_guard(message, layer):
            return
        try:
            await message.bot.send_chat_action(message.chat.id, "typing")
        except Exception as e:
            logger.debug("%s optional failed: %s", 'commands_admin', e, exc_info=True)
        rep = await run_connectivity_checks()
        tg = rep.get("telegram") or {}
        orr = rep.get("openrouter") or {}
        m0 = rep.get("mem0") or {}
        m0m = rep.get("mem0_mirror") or {}
        parts = [
            "<b>🌐 Сеть и ключи</b>",
            f"Таймаут запросов: <code>{esc(rep.get('timeout_sec'))}</code> с (см. CONNECTIVITY_CHECK_TIMEOUT_SEC)",
            f"Итог: <b>{'OK' if rep.get('ok') else 'FAIL'}</b>",
            "",
            f"<i>{esc(rep.get('summary', ''))}</i>",
            "",
            "<b>Telegram</b>",
            f"{esc(tg.get('user_message', '—'))}",
        ]
        if tg.get("username"):
            parts.append(f"username: <code>{esc(tg.get('username'))}</code>")
        if tg.get("error_code"):
            parts.append(f"code: <code>{esc(tg.get('error_code'))}</code>")
        parts.extend(["", "<b>OpenRouter</b>", f"{esc(orr.get('user_message', '—'))}"])
        if orr.get("model"):
            parts.append(f"model: <code>{esc(orr.get('model'))}</code>")
        if orr.get("reply_preview"):
            pv = str(orr.get("reply_preview") or "").replace("\n", " ").replace("\r", " ").strip()
            if len(pv) > 140:
                pv = pv[:137] + "…"
            parts.append(f"preview: <code>{esc(pv)}</code>")
        if orr.get("error_code"):
            parts.append(f"code: <code>{esc(orr.get('error_code'))}</code>")
        parts.extend(
            [
                "",
                "<b>Mem0 (primary)</b>",
                f"{esc(m0.get('user_message', '—'))}",
            ]
        )
        if m0.get("error_code"):
            parts.append(f"code: <code>{esc(m0.get('error_code'))}</code>")
        if m0.get("http_status") is not None:
            parts.append(f"HTTP: <code>{esc(m0.get('http_status'))}</code>")
        parts.extend(
            [
                "",
                "<b>Mem0 (mirror)</b>",
                f"{esc(m0m.get('user_message', '—'))}",
            ]
        )
        if m0m.get("error_code"):
            parts.append(f"code: <code>{esc(m0m.get('error_code'))}</code>")
        if m0m.get("http_status") is not None:
            parts.append(f"HTTP: <code>{esc(m0m.get('http_status'))}</code>")
        ph = rep.get("plugin_http_probes") or {}
        prow = ph.get("results") if isinstance(ph, dict) else None
        if isinstance(ph, dict) and ph.get("skipped"):
            parts.extend(
                [
                    "",
                    "<b>Плагины и внешние HTTP</b>",
                    "<i>Пропущено: <code>CONNECTIVITY_SKIP_PLUGIN_HTTP_PROBES</code>.</i>",
                ]
            )
        elif isinstance(ph, dict) and ph.get("error"):
            parts.extend(["", "<b>Плагины (HTTP)</b>", f"<code>{esc(ph.get('error'))}</code>"])
        elif isinstance(prow, list) and prow:
            parts.extend(["", "<b>Плагины и внешние HTTP</b>"])
            for row in prow:
                if not isinstance(row, dict):
                    continue
                nm = str(row.get("name") or "?")
                if row.get("ok"):
                    ms = row.get("roundtrip_ms")
                    st = row.get("http_status")
                    if ms is not None:
                        parts.append(
                            f"<code>{esc(nm)}</code>: OK, {esc(ms)} ms"
                            + (f", HTTP {esc(st)}" if st is not None else "")
                        )
                    else:
                        parts.append(f"<code>{esc(nm)}</code>: OK")
                else:
                    err = row.get("error") or row.get("http_status") or "fail"
                    parts.append(f"<code>{esc(nm)}</code>: <b>FAIL</b> — <code>{esc(err)}</code>")
        elif isinstance(prow, list) and not prow:
            parts.extend(
                [
                    "",
                    "<b>Плагины и внешние HTTP</b>",
                    "<i>Нет целей в env — доп. GET не выполнялись. Задайте при необходимости: "
                    "<code>SEARXNG_INSTANCE_URL</code>, <code>QDRANT_URL</code>, "
                    "<code>TAVILY_API_KEY</code>, <code>BRAVE_SEARCH_API_KEY</code>, "
                    "<code>VOICE_STT_API_URL</code>, <code>URL_FETCH_MIRROR_BASE</code> "
                    "или <code>CONNECTIVITY_EXTRA_HTTP_PROBES</code>.</i>",
                ]
            )
        parts.extend(["", "<i>JSON: <code>/admin_connectivity_json</code></i>"])
        await reply_html_chunks(message, "\n".join(parts))

    @dp.message(Command("admin_connectivity_json", ignore_mention=True))
    async def handle_admin_connectivity_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        rep = await run_connectivity_checks()
        await reply_json_chunks(message, rep, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_code_map", ignore_mention=True))
    async def handle_admin_code_map(message: Message):
        if not await _admin_guard(message, layer):
            return
        try:
            await message.bot.send_chat_action(message.chat.id, "typing")
        except Exception as e:
            logger.debug("%s optional failed: %s", 'commands_admin', e, exc_info=True)
        def _run():
            return scan_and_maybe_record(persist=True)

        res = await asyncio.to_thread(_run)
        await reply_html_chunks(message, format_code_map_html(res.snapshot))

    @dp.message(Command("admin_code_map_json", ignore_mention=True))
    async def handle_admin_code_map_json(message: Message):
        if not await _admin_guard(message, layer):
            return

        def _run():
            return build_bundle_slice(persist=False)

        payload = await asyncio.to_thread(_run)
        await reply_json_chunks(message, payload, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_code_baseline_set", ignore_mention=True))
    async def handle_admin_code_baseline_set(message: Message):
        if not await _admin_guard(message, layer):
            return

        def _run():
            files = scan_python_sources()
            return save_baseline(files)

        out = await asyncio.to_thread(_run)
        if out.get("ok"):
            await message.answer(
                sanitize_html(f"Эталон кода записан: <code>{out.get('path')}</code>, файлов: <b>{out.get('file_count')}</b>"),
                parse_mode="HTML",
            )
        else:
            await message.answer(f"Не удалось: {out}")

    @dp.message(Command("admin_code_baseline_diff_json", ignore_mention=True))
    async def handle_admin_code_baseline_diff_json(message: Message):
        if not await _admin_guard(message, layer):
            return

        def _run():
            files = scan_python_sources()
            return compare_to_baseline(files, baseline_path())

        rep = await asyncio.to_thread(_run)
        await reply_json_chunks(message, rep, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_logs", ignore_mention=True))
    async def handle_admin_logs(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split()
        n = 25
        comp: str | None = None
        if len(parts) >= 2:
            if parts[1].isdigit():
                n = int(parts[1])
                if len(parts) >= 3:
                    comp = (parts[2] or "").strip() or None
            else:
                comp = (parts[1] or "").strip() or None
        if n < 1 or n > 100:
            await message.answer(
                "Использование: /admin_logs [N] [component] — N от 1 до 100, по умолчанию 25. "
                "Пример: /admin_logs 40 voice",
            )
            return
        snap = layer._admin_module.admin_logs_snapshot(n, component=comp)
        body = snap["body"]
        fm = snap.get("file_meta") or {}
        header = format_admin_logs_header_html(
            snap["n"],
            log_path=str(fm.get("path") or ""),
            file_mtime_utc=str(fm.get("mtime_utc") or ""),
            file_exists=bool(fm.get("exists")),
            newest_ts=str(snap.get("newest_ts") or ""),
            component_filter=str(snap.get("component_filter") or ""),
        )
        single = f"{header}\n\n" + code_block_html(body)
        if len(single) <= 3900:
            await reply_html_chunks(message, single)
        else:
            await reply_html_chunks(message, header)
            await reply_code_plain_chunks(message, body)

    @dp.message(Command("admin_backup_list", ignore_mention=True))
    async def handle_admin_backup_list(message: Message):
        if not await _admin_guard(message, layer):
            return
        ra = layer.orchestrator._recovery_autonomy
        await reply_html_chunks(message, format_backup_list_html(ra.list_backups()))

    @dp.message(Command("admin_backup_list_json", ignore_mention=True))
    async def handle_admin_backup_list_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        ra = layer.orchestrator._recovery_autonomy
        await reply_json_chunks(message, ra.list_backups(), ensure_ascii=False, indent=2)

    @dp.message(Command("admin_backup_run", ignore_mention=True))
    async def handle_admin_backup_run(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split(maxsplit=1)
        reason = parts[1].strip() if len(parts) > 1 else "manual_admin"
        ra = layer.orchestrator._recovery_autonomy
        result = ra.manual_backup(reason=reason)
        await reply_html_chunks(message, format_action_result_html("Бэкап", result))

    @dp.message(Command("admin_restore", ignore_mention=True))
    async def handle_admin_restore(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Использование: /admin_restore latest | backup_<timestamp>_<hex>")
            return
        token = parts[1].strip()
        bid = resolve_bundle_id(token)
        if not bid:
            await message.answer("Бэкап не найден или не прошёл проверку целостности.")
            return
        ra = layer.orchestrator._recovery_autonomy
        result = ra.manual_restore(bid)
        await reply_html_chunks(message, format_action_result_html("Восстановление", result))

    @dp.message(Command("admin_purge_logs", ignore_mention=True))
    async def handle_admin_purge_logs(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split(maxsplit=1)
        full = False
        if len(parts) > 1:
            flag = parts[1].strip().lower()
            full = flag in ("all", "full", "полностью", "все")
        result = DG.purge_runtime_logs(full=full)
        if result.get("ok") and full:
            rc = getattr(layer.orchestrator, "_resilience", None)
            if rc is not None and rc.is_enabled() and rc.is_safe_mode():
                rc.exit_safe_mode("admin: full error journal purge")
                result["safe_mode_cleared"] = True
        await reply_html_chunks(message, format_purge_result_html(result))

    @dp.message(Command("admin_facts", ignore_mention=True))
    async def handle_admin_facts(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Использование: /admin_facts <user_id>")
            return
        data = layer._admin_module.user_facts_summary(parts[1].strip())
        await reply_html_chunks(message, format_admin_user_facts_html(data))

    @dp.message(Command("admin_toggle_skill", ignore_mention=True))
    async def handle_admin_toggle_skill(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "Использование: <code>/admin_toggle_skill &lt;имя&gt;</code> "
                "или <code>/admin_toggle_skill translator on|off</code>",
                parse_mode="HTML",
            )
            return
        try:
            from core.brain import _skills  # type: ignore
            from modules.skills.registry import parse_skill_toggle_args

            skill_name, force = parse_skill_toggle_args(parts[1])
            if not skill_name:
                await message.answer("Укажите имя навыка, например: <code>translator</code>", parse_mode="HTML")
                return
            if force is None:
                state = _skills.toggle(skill_name)
            else:
                state = _skills.set_enabled(skill_name, force)
            on_off = "включён" if state else "выключен"
            await message.answer(
                sanitize_html(f"Навык <code>{esc(skill_name)}</code> — {on_off}."),
                parse_mode="HTML",
            )
        except KeyError:
            known = ", ".join(sorted(getattr(_skills, "status", lambda: {})().keys())[:24])
            await message.answer(
                sanitize_html(
                    f"Навык <code>{esc(skill_name)}</code> не найден."
                    + (f"\n\nДоступные: <code>{esc(known)}</code>" if known else "")
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            await message.answer(f"Ошибка переключения навыка: {e}")

    @dp.message(Command("admin_plugin_disable", ignore_mention=True))
    async def handle_admin_plugin_disable(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Использование: /admin_plugin_disable <plugin_name>")
            return
        plugin_name = normalize_plugin_name(parts[1])
        reg = getattr(layer, "plugin_registry", None)
        if reg is None:
            await message.answer("Реестр плагинов недоступен.")
            return
        if not reg.get_module(plugin_name):
            await message.answer(sanitize_html(f"Плагин <code>{esc(plugin_name)}</code> не найден."), parse_mode="HTML")
            return
        ok = reg.disable_module(plugin_name)
        if ok:
            await message.answer(sanitize_html(f"Плагин <code>{esc(plugin_name)}</code> отключён."), parse_mode="HTML")
        else:
            await message.answer(sanitize_html(f"Не удалось отключить <code>{esc(plugin_name)}</code>."), parse_mode="HTML")

    @dp.message(Command("admin_plugin_delete", ignore_mention=True))
    async def handle_admin_plugin_delete(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.answer(
                "Использование: /admin_plugin_delete <plugin_name> [force]\n"
                "По умолчанию удаляются только user_requested_plugin*.",
            )
            return
        plugin_name = normalize_plugin_name(parts[1])
        force = any(p.lower() in {"force", "--force"} for p in parts[2:])
        if not force and not is_generated_plugin_name(plugin_name):
            await message.answer(
                "Для безопасности без force можно удалять только user_requested_plugin*.\n"
                "Если уверены: /admin_plugin_delete <plugin_name> force",
            )
            return
        reg = getattr(layer, "plugin_registry", None)
        if reg is None:
            await message.answer("Реестр плагинов недоступен.")
            return
        modules_root = Path(getattr(reg, "modules_path", "./modules"))
        target_dir = safe_plugin_dir(modules_root, plugin_name)
        if target_dir is None:
            await message.answer("Некорректное имя плагина.")
            return
        existed_in_registry = bool(reg.get_module(plugin_name))
        reg.disable_module(plugin_name)
        if plugin_name in getattr(reg, "loaded_modules", {}):
            reg.loaded_modules.pop(plugin_name, None)
        if plugin_name in getattr(reg, "modules", {}):
            reg.modules.pop(plugin_name, None)
        if not target_dir.exists():
            state = "удалён из реестра" if existed_in_registry else "не найден на диске"
            await message.answer(
                sanitize_html(f"Плагин <code>{esc(plugin_name)}</code>: {state}."),
                parse_mode="HTML",
            )
            return
        try:
            await asyncio.to_thread(shutil.rmtree, target_dir)
        except Exception as e:
            await message.answer(f"Ошибка удаления каталога: {e}")
            return
        await message.answer(
            sanitize_html(f"Плагин <code>{esc(plugin_name)}</code> удалён: реестр + каталог <code>{esc(str(target_dir))}</code>."),
            parse_mode="HTML",
        )

    @dp.message(Command("admin_anti_flood", ignore_mention=True))
    async def handle_admin_anti_flood(message: Message):
        if not await _admin_guard(message, layer):
            return
        await reply_html_chunks(message, format_anti_flood_html(layer._admin_module.anti_flood_summary()))

    @dp.message(Command("admin_group_mode", ignore_mention=True))
    async def handle_admin_group_mode(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            pol = load_group_chat_policy()
            mode = str(pol.get("participate_mode") or "mention")
            await message.answer(
                sanitize_html(
                    f"Текущий group mode: <b>{esc(mode)}</b>\n"
                    "mention — только @бот / реплай / команды\n"
                    "balanced — + вопросы в чате и имя бота без @\n"
                    "active — на каждое сообщение\n"
                    "Использование: /admin_group_mode mention|balanced|active"
                ),
                parse_mode="HTML",
            )
            return
        arg = parts[1].strip().lower()
        if arg in {"on", "1", "true", "active", "all"}:
            pol = save_group_chat_policy({"participate_mode": "active"})
        elif arg in {"off", "0", "false", "strict", "mention", "listen", "passive"}:
            pol = save_group_chat_policy({"participate_mode": "mention"})
        elif arg in {"balanced", "smart", "questions", "mid", "medium"}:
            pol = save_group_chat_policy({"participate_mode": "balanced"})
        else:
            await message.answer(
                "Использование: /admin_group_mode mention|balanced|active"
            )
            return
        mode = str(pol.get("participate_mode") or "mention")
        await message.answer(sanitize_html(f"Group mode установлен: <b>{esc(mode)}</b>"), parse_mode="HTML")

    @dp.message(Command("admin_group_memory", ignore_mention=True))
    async def handle_admin_group_memory(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            pol = load_group_chat_policy()
            await message.answer(
                sanitize_html(
                    f"Текущая краткая память группы: <b>{esc(pol.get('group_memory_max'))}</b> сообщений.\n"
                    "Использование: /admin_group_memory <4..40> (рекомендуется 12)"
                ),
                parse_mode="HTML",
            )
            return
        try:
            n = int(parts[1].strip())
        except ValueError:
            await message.answer("Нужно число: /admin_group_memory 12")
            return
        pol = save_group_chat_policy({"group_memory_max": n})
        await message.answer(
            sanitize_html(f"Краткая память группы установлена: <b>{esc(pol.get('group_memory_max'))}</b> сообщений."),
            parse_mode="HTML",
        )

    @dp.message(Command("admin_passport", ignore_mention=True))
    async def handle_admin_passport(message: Message):
        if not await _admin_guard(message, layer):
            return
        await reply_html_chunks(message, layer._admin_module.passport_teaser_html())
        payload = layer._admin_module.development_passport_view()
        await reply_html_chunks(message, format_development_passport_block_html(payload))

    @dp.message(Command("admin_passport_set", ignore_mention=True))
    async def handle_admin_passport_set(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "Использование: /admin_passport_set <json> — объект с ключами "
                "mission, evolution_vectors, priorities, kpi_targets, stop_rules (можно частично)."
            )
            return
        raw = parts[1].strip()
        try:
            patch = json.loads(raw)
        except json.JSONDecodeError as e:
            await message.answer(f"Невалидный JSON: {e}")
            return
        try:
            saved = save_passport_patch(patch)
        except ValueError as e:
            await message.answer(f"Ошибка валидации: {e}")
            return
        except OSError as e:
            await message.answer(f"Не удалось записать файл: {e}")
            return
        await reply_html_chunks(message, format_action_result_html("Паспорт сохранён", {"ok": True, "saved": saved}))

    @dp.message(Command("auto_suggestions", ignore_mention=True))
    async def handle_auto_suggestions(message: Message):
        if not await _admin_guard(message, layer):
            return
        rows = layer._autonomy.auto_suggestions()
        await reply_html_chunks(message, format_auto_suggestions_html(rows))

    @dp.message(Command("auto_idea", ignore_mention=True))
    async def handle_auto_idea(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split(maxsplit=1)
        topic = parts[1].strip() if len(parts) > 1 else "general"
        await reply_html_chunks(message, format_auto_idea_html(layer._autonomy.idea(topic)))

    @dp.message(Command("auto_review", ignore_mention=True))
    async def handle_auto_review(message: Message):
        if not await _admin_guard(message, layer):
            return
        payload = {
            "diagnostics": layer._autonomy.auto_diagnostics(),
            "optimize_hints": layer._autonomy.auto_optimize_hints(),
            "suggestions": layer._autonomy.auto_suggestions(),
        }
        await reply_html_chunks(message, format_auto_review_html(payload))

    @dp.message(Command("admin_passport_json", ignore_mention=True))
    async def handle_admin_passport_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        await reply_json_chunks(
            message,
            layer._admin_module.development_passport_view(),
            ensure_ascii=False,
            indent=2,
        )

    @dp.message(Command("remember_patch", ignore_mention=True))
    async def handle_remember_patch(message: Message):
        await run_remember_patch(layer, message)

    @dp.message(Command("forget_patch", ignore_mention=True))
    async def handle_forget_patch(message: Message):
        await run_forget_patch(layer, message)

    @dp.message(Command("clear_all_patches", ignore_mention=True))
    async def handle_clear_all_patches(message: Message):
        await run_clear_all_patches(layer, message)

    @dp.message(Command("export_patches", ignore_mention=True))
    async def handle_export_patches(message: Message):
        await run_export_patches(layer, message)

    @dp.message(Command("list_patches", ignore_mention=True))
    async def handle_list_patches(message: Message):
        await run_list_patches(layer, message)

    @dp.message(Command("pending_suggested_patch", ignore_mention=True))
    async def handle_pending_suggested_patch(message: Message):
        await run_pending_suggested_patch(layer, message)

    @dp.message(Command("approve_suggested_patch", ignore_mention=True))
    async def handle_approve_suggested_patch(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer(sanitize_html("Использование: /approve_suggested_patch &lt;id&gt;"), parse_mode="HTML")
            return
        pid = parts[1].strip()
        le = pending_approve(pid)
        if le:
            await message.answer(
                sanitize_html(f"В латки: <code>{esc(str(le.get('id')))}</code>"),
                parse_mode="HTML",
            )
        else:
            await message.answer("id не найден или уже обработан.")

    @dp.message(Command("dismiss_suggested_patch", ignore_mention=True))
    async def handle_dismiss_suggested_patch(message: Message):
        if not await _admin_guard(message, layer):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer(sanitize_html("Использование: /dismiss_suggested_patch &lt;id&gt;"), parse_mode="HTML")
            return
        if pending_dismiss(parts[1].strip()):
            await message.answer("Снято с очереди.")
        else:
            await message.answer("id не найден.")

    # ── /admin_git ──
    async def _run_git_cmd(args: list[str], cwd: Path, timeout: int = 120) -> tuple[bool, str, str]:
        """Выполнить git-команду, вернуть (ok, stdout, stderr)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", *args,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            ok = proc.returncode == 0
            return ok, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")
        except Exception as e:
            return False, "", str(e)

    @dp.message(Command("admin_git", ignore_mention=True))
    async def handle_admin_git(message: Message):
        if not await _admin_guard(message, layer):
            return

        text = (message.text or "").strip()
        parts = text.split(maxsplit=1)
        commit_msg = parts[1].strip() if len(parts) >= 2 else ""
        if not commit_msg:
            await message.answer(
                sanitize_html(
                    "Использование: <code>/admin_git &lt;commit message&gt;</code> — "
                    "CHANGELOG → git add → commit → push → pull."
                ),
                parse_mode="HTML",
            )
            return

        status_msg = await message.answer("⏳ Запуск git pipeline…")
        project_root = Path(os.environ.get("GEMMA_BOT_DIR") or Path.cwd()).resolve()

        # 1. git add -A
        await status_msg.edit_text("⏳ git add -A…")
        add_ok, add_out, add_err = await _run_git_cmd(["add", "-A"], project_root, 60)
        if not add_ok:
            await status_msg.edit_text(
                sanitize_html(f"<b>git add</b> — ошибка\n<code>{esc(add_err or add_out)}</code>"),
                parse_mode="HTML",
            )
            return

        # 2. git commit
        await status_msg.edit_text("⏳ git commit…")
        commit_ok, commit_out, commit_err = await _run_git_cmd(
            ["commit", "-m", commit_msg], project_root, 60
        )
        if not commit_ok:
            msg = commit_err or commit_out
            if "nothing to commit" in msg.lower() or "no changes" in msg.lower():
                await status_msg.edit_text(
                    sanitize_html(f"<b>git commit</b> — нет изменений для коммита.\n<code>{esc(msg[:800])}</code>"),
                    parse_mode="HTML",
                )
                return
            await status_msg.edit_text(
                sanitize_html(f"<b>git commit</b> — ошибка\n<code>{esc(msg[:1000])}</code>"),
                parse_mode="HTML",
            )
            return

        # 3. git push
        await status_msg.edit_text("⏳ git push…")
        push_ok, push_out, push_err = await _run_git_cmd(["push"], project_root, 180)
        if not push_ok:
            await status_msg.edit_text(
                sanitize_html(
                    f"<b>git commit</b> — OK\n{esc((commit_out or '')[:500])}\n\n"
                    f"<b>git push</b> — ошибка\n<code>{esc(push_err or push_out)}</code>"
                ),
                parse_mode="HTML",
            )
            return

        # 4. git pull (sync after push)
        await status_msg.edit_text("⏳ git pull (синхронизация)…")
        pull_ok, pull_out, pull_err = await _run_git_cmd(["pull"], project_root, 120)
        pull_line = ""
        if not pull_ok:
            pull_line = f"\n\n⚠️ git pull после push: {esc(pull_err[:500])}"

        # Success
        report = (
            f"<b>git pipeline — OK</b>\n"
            f"<b>commit:</b> {esc(commit_msg[:200])}\n"
            f"{esc((commit_out or '').strip()[:500])}\n"
            f"<b>push:</b> OK{pull_line}"
        )
        await status_msg.edit_text(sanitize_html(report), parse_mode="HTML")

    @dp.message(Command("admin_autonomy", ignore_mention=True))
    async def handle_admin_autonomy(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.admin_autonomy import build_autonomy_report

        rep = build_autonomy_report()
        lines: list[str] = ["<b>🤖 Автономность системы</b>\n"]

        # Reflexion lessons
        n_lessons = rep.get("reflexion_lessons_active", -1)
        icon = _fmt_icon(n_lessons)
        lines.append(f"{icon} Уроки reflexion: <b>{_fmt_val(n_lessons)}</b>")

        # Qdrant etalons
        n_etalons = rep.get("qdrant_etalons_count", -1)
        icon = _fmt_icon(n_etalons)
        lines.append(f"{icon} Эталонов в Qdrant: <b>{_fmt_val(n_etalons)}</b>")

        # Classifier
        cls = rep.get("classifier", {})
        h = cls.get("hits", 0)
        m = cls.get("misses", 0)
        e = cls.get("errors", 0)
        total = h + m
        hit_pct = round(h / max(total, 1) * 100, 1) if total else 0
        lines.append(f"📊 Классификатор: hits=<b>{h}</b> miss=<b>{m}</b> err=<b>{e}</b> rate=<b>{hit_pct}%</b>")

        # Sanitizer
        n_san = rep.get("sanitizer_removed_total", -1)
        icon = _fmt_icon(n_san)
        lines.append(f"{icon} Санитайзер удалил сообщений: <b>{_fmt_val(n_san)}</b>")

        # Cache
        cache = rep.get("cache", {})
        cov = cache.get("cache_coverage_pct", "?")
        hr = cache.get("hit_rate_pct", "?")
        lines.append(f"⚡ Cache: coverage=<b>{cov}%</b> hit_rate=<b>{hr}%</b>")

        # Uptime hint
        uptime = rep.get("uptime_hint_sec", 0)
        if uptime:
            lines.append(f"⏱ Uptime: <b>{uptime // 3600}ч {(uptime % 3600) // 60}м</b>")

        await reply_html_chunks(message, "\n".join(lines))

    @dp.message(Command("admin_run_learning", ignore_mention=True))
    async def handle_admin_run_learning(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.learning_maintenance import format_learning_maintenance_html, maybe_run_learning_maintenance

        rep = maybe_run_learning_maintenance(force=True)
        await reply_html_chunks(message, format_learning_maintenance_html(rep))

    @dp.message(Command("admin_run_learning_json", ignore_mention=True))
    async def handle_admin_run_learning_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.learning_maintenance import maybe_run_learning_maintenance

        rep = maybe_run_learning_maintenance(force=True)
        await reply_json_chunks(message, rep, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_learning_digest", ignore_mention=True))
    async def handle_admin_learning_digest(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.learning_digest import build_learning_digest, format_learning_digest_html

        raw = (command.args or "").strip()
        if raw.lower() == "json":
            digest = build_learning_digest(user_id=effective_admin_user_id(message, ""))
            await reply_json_chunks(message, digest, ensure_ascii=False, indent=2)
            return
        uid = effective_admin_user_id(message, raw)
        digest = build_learning_digest(user_id=uid)
        await reply_html_chunks(message, format_learning_digest_html(digest))

    @dp.message(Command("admin_learning_digest_json", ignore_mention=True))
    async def handle_admin_learning_digest_json(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.learning_digest import build_learning_digest

        raw = (command.args or "").strip()
        if raw.lower() == "json":
            uid = effective_admin_user_id(message, "")
        else:
            uid = effective_admin_user_id(message, raw)
        digest = build_learning_digest(user_id=uid)
        await reply_json_chunks(message, digest, ensure_ascii=False, indent=2)

    def _route_risk_clusters_pack(args: str) -> Dict[str, Any]:
        from core.route_risk_cluster import cluster_route_risk_recent

        hours = 6.0
        if args and args.strip():
            try:
                hours = float(args.strip().split()[0])
            except ValueError:
                pass
        return cluster_route_risk_recent(hours=hours, min_count=2)

    @dp.message(Command("admin_route_risk_clusters", ignore_mention=True))
    async def handle_admin_route_risk_clusters(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        from core.admin_route_risk_view import format_route_risk_clusters_html

        pack = _route_risk_clusters_pack(command.args or "")
        await reply_html_chunks(message, format_route_risk_clusters_html(pack))

    @dp.message(Command("admin_route_risk_clusters_json", ignore_mention=True))
    async def handle_admin_route_risk_clusters_json(message: Message, command: CommandObject):
        if not await _admin_guard(message, layer):
            return
        pack = _route_risk_clusters_pack(command.args or "")
        await reply_json_chunks(message, pack, ensure_ascii=False, indent=2)

    @dp.message(Command("admin_event_bus_history", ignore_mention=True))
    async def handle_admin_event_bus_history(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.event_bus import bus

        hist = bus.history(n=30)
        lines = ["<b>⚡ EventBus — последние события</b>\n"]
        if not hist:
            lines.append("(нет событий)")
        else:
            for ev in hist[-20:]:
                et = esc(ev.event_type)
                ts = esc(str(ev.data.get("ts", ""))[:19])
                cid = ev.correlation_id[:8]
                lines.append(f"<code>{ts}</code> <b>{et}</b> cid={cid}")
        await reply_html_chunks(message, "\n".join(lines))

    @dp.message(Command("admin_event_bus_healers", ignore_mention=True))
    async def handle_admin_event_bus_healers(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.event_bus import bus
        from core.event_healers import healers_snapshot, get_module_failure_healer

        snap = healers_snapshot()
        lines = ["<b>⚡ Healers — состояние</b>\n"]
        mh = snap.get("module_failure_healer", {})
        lines.append(f"<b>ModuleFailureHealer</b> (auto-disable при {mh.get('auto_disable_at', 5)} падений):")
        for mod, cnt in mh.get("failures", {}).items():
            lines.append(f"  {mod}: {cnt} падений")
        patches = mh.get("patches_created", [])
        if patches:
            lines.append(f"  🩹 Патчей создано: {len(patches)}")
        disabled = mh.get("disabled", [])
        if disabled:
            lines.append(f"  ⛔ Авто-отключено модулей: {', '.join(disabled)}")

        ah = snap.get("anomaly_escalator", {})
        lines.append(f"\n<b>AnomalyEscalator:</b>")
        for code, cnt in ah.get("recent", {}).items():
            lines.append(f"  {code}: {cnt} в окне {ah.get('window_sec', 300)}с")

        la = snap.get("auto_latency_healer", {})
        lines.append(f"\n<b>AutoLatencyHealer</b> (p95 > {la.get('p95_threshold_ms', 10000)}ms):")
        lines.append(f"  Выборок: {la.get('samples', 0)}, p95: {la.get('p95_ms', 0)}ms")
        lines.append(f"  Срабатываний: {la.get('actions_taken', 0)} (cooldown {la.get('cooldown_sec', 300)}с)")

        fr = snap.get("auto_fail_ratio_healer", {})
        lines.append(f"\n<b>AutoFailRatioHealer</b> (порог {fr.get('threshold', 0.3)}):")
        lines.append(f"  Выборок: {fr.get('samples', 0)}, fail/ok: {fr.get('fail_ratio', 0)}")
        lines.append(f"  Срабатываний: {fr.get('actions_taken', 0)}")

        hp = snap.get("auto_host_pressure_healer", {})
        lines.append(f"\n<b>AutoHostPressureHealer</b> (cooldown {hp.get('cooldown_sec', 600)}с):")
        lines.append(f"  Срабатываний: {hp.get('actions_taken', 0)}")

        lines.append(f"\nУстановлен: {'да' if snap.get('installed') else 'нет'}")
        sub = bus.subscriber_count()
        if sub:
            lines.append(f"Подписчики: {len(sub)} типов")
        await reply_html_chunks(message, "\n".join(lines))

    @dp.message(Command("admin_bug_self_heal", ignore_mention=True))
    async def handle_admin_bug_self_heal(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.event_bus import bus

        hist = bus.history(n=20, event_type="healer.action")
        lines = ["<b>💊 Self-heal — ретроспектива</b>\n"]
        if not hist:
            lines.append("(нет записей о лечении)")
        else:
            for ev in hist:
                d = ev.data
                healer = esc(d.get("healer", "?"))
                action = esc(d.get("action", "?"))
                reason = esc(d.get("reason", ""))[:200]
                ts = esc(str(d.get("ts", ""))[:19])
                lines.append(f"<code>{ts}</code> <b>{healer}</b> → {action}: {reason}")
        await reply_html_chunks(message, "\n".join(lines))

    @dp.message(Command("admin_undo_log", ignore_mention=True))
    async def handle_admin_undo_log(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.auto_rollback import get_undo_log

        entries = get_undo_log().list_all(limit=30)
        lines = ["<b>↩️ UndoLog — журнал авто-лечения</b>\n"]
        if not entries:
            lines.append("(нет записей)")
        else:
            for e in entries:
                rid = esc(e.id[:8])
                ts = esc(time.strftime("%H:%M:%S", time.localtime(e.ts)))
                status = esc(e.status)
                healer = esc(e.healer)
                action = esc(e.action)
                reason = esc(e.rollback_reason or "")[:80]
                module = esc(e.params.get("module") or e.params.get("key") or "")
                lines.append(
                    f"<code>{ts}</code> <b>{healer}</b> → {action} [{status}]"
                    + (f"\n  id={rid} target={module}" if module else f"\n  id={rid}")
                    + (f"\n  ⚠️ {reason}" if reason else "")
                )
        lines.append(
            "\nПодтвердить: <code>/admin_undo_confirm &lt;id&gt;</code>\n"
            "Откатить: <code>/admin_undo_rollback &lt;id&gt;</code>"
        )
        await reply_html_chunks(message, "\n".join(lines))

    @dp.message(Command("admin_undo_confirm", ignore_mention=True))
    async def handle_admin_undo_confirm(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.auto_rollback import get_undo_log

        cmd = message.text or ""
        parts = cmd.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Укажи ID: /admin_undo_confirm <id>")
            return
        entry_id = parts[1].strip()
        # Поиск по префиксу
        all_entries = get_undo_log().list_all(limit=200)
        match = next((e for e in all_entries if e.id.startswith(entry_id)), None)
        if not match:
            await message.answer(f"Запись <code>{esc(entry_id)}</code> не найдена.")
            return
        if get_undo_log().confirm(match.id):
            await message.answer(f"✅ Подтверждено: <code>{esc(match.id[:8])}</code>")
        else:
            await message.answer("Не удалось подтвердить (возможно, уже обработана).")

    @dp.message(Command("admin_undo_rollback", ignore_mention=True))
    async def handle_admin_undo_rollback(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.auto_rollback import get_rollback_engine, get_undo_log

        cmd = message.text or ""
        parts = cmd.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Укажи ID: /admin_undo_rollback <id>")
            return
        entry_id = parts[1].strip()
        all_entries = get_undo_log().list_all(limit=200)
        match = next((e for e in all_entries if e.id.startswith(entry_id)), None)
        if not match:
            await message.answer(f"Запись <code>{esc(entry_id)}</code> не найдена.")
            return
        engine = get_rollback_engine()
        await engine._do_rollback(match, "admin_manual_rollback")
        await message.answer(f"🔄 Откат выполнен: <code>{esc(match.id[:8])}</code>")

    @dp.message(Command("admin_event_bus_subscribers", ignore_mention=True))
    async def handle_admin_event_bus_subscribers(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.event_bus import bus

        subs = bus.subscriber_count()
        lines = ["<b>⚡ EventBus — подписчики</b>\n"]
        if not subs:
            lines.append("(нет подписчиков)")
        else:
            for et, cnt in sorted(subs.items()):
                lines.append(f"  <b>{esc(et)}</b> → {cnt}")
        await reply_html_chunks(message, "\n".join(lines))

    @dp.message(Command("admin_bug_heal_triage", ignore_mention=True))
    async def handle_admin_bug_heal_triage(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.llm_triage import run_triage_now_async, get_collector

        collector = get_collector()
        pending = collector.pending_count()
        if pending == 0:
            await message.answer("Нет накопленных событий для триажа. "
                                   "Дождись срабатывания healers или проверь /admin_event_bus_healers.")
            return
        await message.answer(f"🧠 Запускаю LLM-триаж по {pending} событию(-ям)…")
        rec_id = await run_triage_now_async()
        if rec_id:
            await message.answer(f"✅ Рекомендация создана: <code>{rec_id}</code>. "
                                   f"Проверь: /admin_bug_heal_list")
        else:
            await message.answer("❌ Триаж не дал результата (LLM не ответил или ошибка).")

    @dp.message(Command("admin_bug_heal_list", ignore_mention=True))
    async def handle_admin_bug_heal_list(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.llm_triage import list_recommendations as list_llm
        from core.meta_cognitive_engine import get_mce

        # LLM Triage рекомендации
        llm_recs = list_llm(limit=10)
        # MCE рекомендации
        mce_recs = get_mce().list_recommendations(limit=10)

        lines = ["<b>🧠 LLM Triage — рекомендации</b>\n"]
        if not llm_recs:
            lines.append("(нет LLM рекомендаций)")
        else:
            for r in llm_recs:
                rid = esc(r.get("id", "?"))
                ts = esc(str(r.get("ts", ""))[:16])
                prio = esc(r.get("priority", "?"))
                status = esc(r.get("status", "?"))
                analysis = esc((r.get("analysis") or "")[:120])
                lines.append(
                    f"<code>{ts}</code> [{prio}] <b>{rid}</b> — {status}\n"
                    f"  {analysis}"
                )

        lines.append("\n<b>🤖 MCE (Meta-Cognitive) — рекомендации</b>\n")
        if not mce_recs:
            lines.append("(нет MCE рекомендаций)")
        else:
            for r in mce_recs:
                rid = esc(r.get("id", "?"))
                ts = esc(time.strftime("%m-%d %H:%M", time.localtime(r.get("ts", 0))))
                status = esc(r.get("status", "?"))
                suggestion = esc((r.get("suggestion") or r.get("analysis", ""))[:120])
                param = esc(r.get("param", "?"))
                old_v = esc(r.get("old_value", "")[:20])
                new_v = esc(r.get("new_value", "")[:20])
                lines.append(
                    f"<code>{ts}</code> [auto] <b>{rid}</b> — {status}\n"
                    f"  {suggestion}\n"
                    f"  {param}: {old_v} → {new_v}"
                )

        lines.append(
            "\nПрименить: <code>/admin_bug_heal_apply &lt;id&gt;</code>\n"
            "Отклонить: <code>/admin_bug_heal_dismiss &lt;id&gt;</code>"
        )
        await reply_html_chunks(message, "\n".join(lines))

    @dp.message(Command("admin_bug_heal_apply", ignore_mention=True))
    async def handle_admin_bug_heal_apply(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.llm_triage import get_recommendation, apply_recommendation
        from core.heal_executor import apply_steps
        from core.meta_cognitive_engine import get_mce

        cmd = message.text or ""
        parts = cmd.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Укажи ID рекомендации: /admin_bug_heal_apply <id>")
            return
        rec_id = parts[1].strip()
        rec = get_recommendation(rec_id)

        # Если не найдено в LLM Triage → проверить MCE
        if not rec:
            mce_recs = get_mce().list_recommendations(limit=50)
            mce_rec = next((r for r in mce_recs if r.get("id") == rec_id), None)
            if mce_rec:
                if mce_rec.get("status") != "pending":
                    await message.answer(f"Статус: {mce_rec.get('status')} — уже обработана.")
                    return
                get_mce().record_recommendation_outcome(rec_id, "applied")
                param = mce_rec.get("param", "")
                new_value = mce_rec.get("new_value", "")
                if param and param != "INTERNAL_LESSON_CLEANUP":
                    os.environ[param] = new_value
                    msg = (
                        f"MCE рекомендация <code>{esc(rec_id)}</code> применена.\n"
                        f"<code>{esc(param)}={esc(new_value)}</code> — env обновлён."
                    )
                elif param == "INTERNAL_LESSON_CLEANUP":
                    try:
                        from core.self_learning.lesson_manager import LessonManager
                        retired = LessonManager.get_instance().apply_forgetting_curve()
                        msg = f"MCE: apply_forgetting_curve → отозвано {retired} уроков."
                    except Exception:
                        msg = "MCE: apply_forgetting_curve не удался."
                else:
                    msg = f"MCE рекомендация <code>{esc(rec_id)}</code> принята (без env)."
                await message.answer(msg)
                return

            await message.answer(f"Рекомендация <code>{esc(rec_id)}</code> не найдена.")
            return

        if rec.get("status") != "pending":
            await message.answer(f"Статус: {rec.get('status')} — уже обработана.")
            return

        steps = rec.get("steps", [])
        if not steps:
            await message.answer("В рекомендации нет шагов для выполнения. "
                                   "Можно отметить как применённую вручную.")
            return

        await message.answer(f"🔄 Применяю {len(steps)} шагов лечения…")
        result = await apply_steps(steps, reason=f"llm_triage:{rec_id}")

        # Отмечаем как применённую
        apply_recommendation(rec_id)
        bus.emit("healer.action", {
            "healer": "admin_bug_heal_apply",
            "action": "applied_recommendation",
            "reason": f"rec_id={rec_id}",
            "details": {"rec_id": rec_id, "steps_ok": result.get("ok")},
        })

        lines = [
            f"<b>🧠 Применение рекомендации {esc(rec_id)}</b>\n",
            f"<b>Статус:</b> {'✅ Все шаги выполнены' if result['ok'] else '❌ Некоторые шаги не удались'}",
            "",
        ]
        for r in result.get("results", []):
            icon = "✅" if r.get("ok") else "❌"
            raw = esc((r.get("_raw") or "")[:150])
            err = esc((r.get("error") or "")[:100])
            line = f"{icon} <code>{raw}</code>"
            if err:
                line += f"\n   ⚠️ {err}"
            lines.append(line)

        await reply_html_chunks(message, "\n".join(lines))

    @dp.message(Command("admin_bug_heal_dismiss", ignore_mention=True))
    async def handle_admin_bug_heal_dismiss(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.llm_triage import get_recommendation, dismiss_recommendation
        from core.meta_cognitive_engine import get_mce

        cmd = message.text or ""
        parts = cmd.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Укажи ID: /admin_bug_heal_dismiss <id>")
            return
        rec_id = parts[1].strip()
        if dismiss_recommendation(rec_id):
            await message.answer(f"Рекомендация <code>{esc(rec_id)}</code> отклонена.")
            return
        # Проверить MCE
        mce_recs = get_mce().list_recommendations(limit=50)
        mce_rec = next((r for r in mce_recs if r.get("id") == rec_id), None)
        if mce_rec:
            get_mce().record_recommendation_outcome(rec_id, "dismissed")
            await message.answer(f"MCE рекомендация <code>{esc(rec_id)}</code> отклонена.")
            return
        await message.answer(f"Не найдена или уже обработана.")

    @dp.message(Command("admin_mce_status", ignore_mention=True))
    async def handle_admin_mce_status(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.admin_mce_status_view import format_mce_status_html
        from core.meta_cognitive_engine import get_mce

        await reply_html_chunks(message, format_mce_status_html(get_mce().snapshot()))

    @dp.message(Command("admin_mce_status_json", ignore_mention=True))
    async def handle_admin_mce_status_json(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.meta_cognitive_engine import get_mce

        await reply_json_chunks(message, get_mce().snapshot(), ensure_ascii=False, indent=2)

    @dp.message(Command("admin_mce_ask", ignore_mention=True))
    async def handle_admin_mce_ask(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.meta_cognitive_engine import get_mce

        cmd = message.text or ""
        parts = cmd.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "Задай вопрос MCE о своём состоянии.\n"
                "Примеры:\n"
                "<code>/admin_mce_ask как дела?</code>\n"
                "<code>/admin_mce_ask какая латентность?</code>\n"
                "<code>/admin_mce_ask что не так?</code>\n"
                "<code>/admin_mce_ask какие цели?</code>"
            )
            return
        question = parts[1].strip()
        answer = get_mce().ask(question)
        await message.answer(f"🤖 <b>MCE:</b>\n{esc(answer)}")

    @dp.message(Command("admin_patch_list", ignore_mention=True))
    async def handle_admin_patch_list(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.code_evolution import get_patch_runner, get_evolution_log

        patches = get_patch_runner().list_patches(limit=15)
        lines = ["<b>🔧 Code Evolution — патчи</b>\n"]
        if not patches:
            lines.append("(нет патчей)")
        else:
            for p in patches:
                pid = esc(p.get("id", "?")[:12])
                ts = time.strftime("%m-%d %H:%M", time.localtime(p.get("ts", 0)))
                status = esc(p.get("status", "?"))
                target = esc(p.get("target_file", "?"))
                desc = esc(p.get("description", "")[:60])
                lines.append(
                    f"<code>{ts}</code> <b>{pid}</b> — {status}\n"
                    f"  {target}: {desc}"
                )
        lines.append(
            "\nДетали: <code>/admin_patch_status &lt;id&gt;</code>\n"
            "Откат: <code>/admin_patch_rollback &lt;id&gt;</code>"
        )
        await reply_html_chunks(message, "\n".join(lines))

    @dp.message(Command("admin_patch_status", ignore_mention=True))
    async def handle_admin_patch_status(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.code_evolution import get_patch_runner

        cmd = message.text or ""
        parts = cmd.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Укажи ID: /admin_patch_status <id>")
            return
        patch_id = parts[1].strip()
        patches = get_patch_runner().list_patches(limit=50)
        p = next((pp for pp in patches if pp.get("id", "").startswith(patch_id)), None)
        if not p:
            await message.answer(f"Патч <code>{esc(patch_id)}</code> не найден.")
            return
        lines = [
            f"<b>Патч {esc(p['id'][:12])}</b>\n",
            f"Файл: <code>{esc(p.get('target_file', '?'))}</code>",
            f"Статус: {esc(p.get('status', '?'))}",
            f"Создан: {time.strftime('%m-%d %H:%M', time.localtime(p.get('ts', 0)))}",
            f"Описание: {esc(p.get('description', '?'))}",
            f"Тесты: {'✅' if p.get('test_ok') else '❌'} {esc(p.get('test_result', '')[:100])}",
        ]
        if p.get("commit_sha"):
            lines.append(f"Коммит: <code>{esc(p['commit_sha'][:12])}</code>")
            lines.append(f"Деплой: {'✅' if p.get('deploy_ok') else '❌'}")
        if p.get("metric_before"):
            lines.append(f"Метрики до: {p['metric_before']}")
        if p.get("metric_after"):
            lines.append(f"Метрики после: {p['metric_after']}")
        if p.get("rolled_back_at"):
            lines.append(f"Откат: {time.strftime('%m-%d %H:%M', time.localtime(p['rolled_back_at']))}")
        await reply_html_chunks(message, "\n".join(lines))

    @dp.message(Command("admin_patch_rollback", ignore_mention=True))
    async def handle_admin_patch_rollback(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.code_evolution import get_patch_runner

        cmd = message.text or ""
        parts = cmd.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Укажи ID: /admin_patch_rollback <id>")
            return
        patch_id = parts[1].strip()
        ok = get_patch_runner().rollback(patch_id)
        if ok:
            await message.answer(f"Патч <code>{esc(patch_id)}</code> откачен.")
        else:
            await message.answer(f"Не удалось откатить <code>{esc(patch_id)}</code>.")

    @dp.message(Command("admin_evol_log", ignore_mention=True))
    async def handle_admin_evol_log(message: Message):
        if not await _admin_guard(message, layer):
            return
        from core.code_evolution import get_evolution_log

        entries = get_evolution_log().recent(limit=15)
        lines = ["<b>📋 Evolution Log</b>\n"]
        if not entries:
            lines.append("(журнал пуст)")
        else:
            for e in entries:
                et = time.strftime("%H:%M", time.localtime(e.get("ts", 0)))
                ev = esc(e.get("event_type", "?"))
                det = esc(str(e.get("details", {}))[:100])
                lines.append(f"<code>{et}</code> <b>{ev}</b>\n  {det}")
        await reply_html_chunks(message, "\n".join(lines))

    def _fmt_val(v: int) -> str:
        if v < 0:
            return "N/A"
        return str(v)

    def _fmt_icon(v: int) -> str:
        if v < 0:
            return "⚠️"
        return "✅"
