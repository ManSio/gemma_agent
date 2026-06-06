"""
Снимок диагностики для мозга (TOOL_CALL): тот же конвейер, что /admin_diagnostic ZIP,
без отправки файла пользователю. Оркестратор задаётся при старте процесса.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_orchestrator: Any = None


def set_orchestrator_for_runtime_diagnostic(orchestrator: Any) -> None:
    global _orchestrator
    _orchestrator = orchestrator


def _max_json_chars() -> int:
    try:
        return max(20_000, int((os.getenv("RUNTIME_DIAG_TOOL_MAX_JSON_CHARS") or "180000").strip()))
    except ValueError:
        return 180_000


def _json_size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str))


def _compact_bundle(bundle: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
    """Урезать тяжёлые поля, пока JSON не влезет в лимит (для контекста LLM)."""
    b: Dict[str, Any] = copy.deepcopy(bundle)
    attempts = 0
    while _json_size(b) > max_chars and attempts < 12:
        attempts += 1
        if b.get("code_cartography") and not (isinstance(b.get("code_cartography"), dict) and b["code_cartography"].get("_omitted")):
            b["code_cartography"] = {"_omitted": True, "hint": "полная карта в /admin_diagnostic ZIP"}
            continue
        re = b.get("runtime_errors_recent")
        if isinstance(re, list) and len(re) > 15:
            b["runtime_errors_recent"] = re[-15:]
            b["runtime_errors_recent_note"] = f"показаны последние 15 из {len(re)}"
            continue
        if b.get("admin_full_system_report") and not (
            isinstance(b.get("admin_full_system_report"), dict) and (b["admin_full_system_report"] or {}).get("_omitted")
        ):
            b["admin_full_system_report"] = {"_omitted": True, "hint": "/admin_system_json"}
            continue
        if b.get("performance") and not (isinstance(b.get("performance"), dict) and (b["performance"] or {}).get("_omitted")):
            b["performance"] = {"_omitted": True}
            continue
        if b.get("boot_timeline") and attempts >= 6:
            b["boot_timeline"] = {"_omitted": True}
            continue
        pl = b.get("process_log_file")
        if isinstance(pl, dict) and pl.get("tail") and attempts >= 5:
            trimmed = {k: v for k, v in pl.items() if k != "tail"}
            trimmed["_tail_omitted"] = True
            trimmed["hint"] = "полный хвост в /admin_diagnostic ZIP (bundle.json)"
            b["process_log_file"] = trimmed
            continue
        ds = b.get("diagnostic_snapshot")
        if isinstance(ds, dict) and ds and attempts >= 8:
            b["diagnostic_snapshot"] = {
                "ts": ds.get("ts"),
                "_truncated": True,
                "monitoring": ds.get("monitoring"),
                "errors": ds.get("errors"),
                "security": ds.get("security"),
            }
            continue
        break
    b["_compacted_for_brain_tool"] = True
    b["_approx_json_chars"] = _json_size(b)
    return b


class RuntimeDiagnosticModule:
    """Инструменты: полный diagnostic bundle как в ZIP (bundle.json)."""

    BRAIN_LITE_INCLUDE = True

    async def collect_diagnostic_bundle(
        self,
        include_connectivity: Any = False,
        max_json_chars: Any = None,
    ) -> Dict[str, Any]:
        """
        Тот же JSON, что кладётся в ZIP (см. build_diagnostic_bundle).
        include_connectivity=true — как /admin_diagnostic net (~десятки секунд).
        """
        ic = include_connectivity
        if isinstance(ic, str):
            ic = ic.strip().lower() in ("1", "true", "yes", "on")
        ic = bool(ic)

        orch = _orchestrator
        if orch is None:
            return {
                "error": "no_orchestrator",
                "hint": "set_orchestrator_for_runtime_diagnostic не вызван при старте",
            }
        lim: int
        if max_json_chars is None or max_json_chars == "":
            lim = _max_json_chars()
        else:
            try:
                lim = max(20_000, int(max_json_chars))
            except (TypeError, ValueError):
                lim = _max_json_chars()
        try:
            from core.admin_module import AdminModule
            from core.diagnostic_bundle import build_diagnostic_bundle

            beh = getattr(orch, "behavior_store", None)
            admin = AdminModule(orchestrator=orch, behavior_store=beh)
            bundle = await build_diagnostic_bundle(
                orch,
                admin,
                include_connectivity=ic,
            )
        except Exception as e:
            logger.warning("collect_diagnostic_bundle failed: %s", e)
            return {"error": str(e)}

        raw_sz = _json_size(bundle)
        out: Dict[str, Any] = dict(bundle)
        out["_tool_meta"] = {
            "approx_json_chars": raw_sz,
            "include_connectivity": ic,
            "same_as_admin_diagnostic_zip": True,
        }
        if raw_sz > lim:
            out = _compact_bundle(out, lim)
            out["_tool_meta"]["compacted_to_max_json_chars"] = lim
        return out
