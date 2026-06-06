"""
Копирование примеров конфигурации оператора в data/runtime при первом запуске или по запросу.

Примеры в репозитории:
  config/system_directive_addon_v3.example.txt → SYSTEM_DIRECTIVE_ADDON_PATH (по умолчанию data/runtime/system_directive_addon.txt)
  (архив v2: config/system_directive_addon_v2.example.txt)
  config/operator_rules.example.json → OPERATOR_RULES_PATH (по умолчанию data/runtime/operator_rules.json)

Переменные окружения:
  RUNTIME_CONFIG_SEED=false — не вызывать автосид при старте (по умолчанию true).
  RUNTIME_DIRECTIVE_SEED_FORCE=true — при старте всегда перезаписывать system_directive_addon.txt из примера.
  operator_rules.json по умолчанию копируется только если файла нет (не перетирать прод).
"""

from __future__ import annotations

import html
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _repo_root() -> Path:
    pr = os.getenv("PROJECT_ROOT", "").strip()
    if pr:
        return Path(pr).resolve()
    return Path(__file__).resolve().parent.parent


def example_directive_path() -> Path:
    return (_repo_root() / "config" / "system_directive_addon_v3.example.txt").resolve()


def example_operator_rules_path() -> Path:
    return (_repo_root() / "config" / "operator_rules.example.json").resolve()


def seed_runtime_config_from_examples(
    *,
    force_directive: bool = False,
    force_operator_rules: bool = False,
) -> Dict[str, Any]:
    """
    Копирует примеры в рантайм. Возвращает отчёт для логов/админки.
    """
    from core.operator_rules import invalidate_operator_rules_cache, rules_path
    from core.system_directive_addon import system_directive_addon_path

    report: Dict[str, Any] = {"directive": None, "operator_rules": None}
    ex_dir = example_directive_path()
    ex_rules = example_operator_rules_path()
    dest_dir = system_directive_addon_path()
    dest_rules = rules_path()

    def _copy_if(src: Path, dest: Path, *, force: bool, label: str) -> Optional[str]:
        if not src.is_file():
            logger.warning("runtime_config_seed: нет примера %s", src)
            return f"skip_no_example:{label}"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error("runtime_config_seed: mkdir %s: %s", dest.parent, e)
            return f"error_mkdir:{label}"
        if dest.is_file() and dest.stat().st_size > 0 and not force:
            return None
        try:
            shutil.copyfile(src, dest)
            logger.info(
                "runtime_config_seed: записан %s ← %s",
                dest,
                src,
                extra={"gemma_event": "runtime_config_seed", "target": label},
            )
            return f"written:{label}"
        except OSError as e:
            logger.error("runtime_config_seed: copy %s: %s", dest, e)
            return f"error_copy:{label}"

    st_dir = _copy_if(ex_dir, dest_dir, force=force_directive, label="system_directive_addon")
    if st_dir:
        report["directive"] = st_dir
    st_rules = _copy_if(ex_rules, dest_rules, force=force_operator_rules, label="operator_rules")
    if st_rules:
        report["operator_rules"] = st_rules
    if st_rules and st_rules.startswith("written"):
        invalidate_operator_rules_cache()
    return report


def format_runtime_seed_report_ru(report: Dict[str, Any]) -> str:
    """Краткий отчёт для Telegram/HTML (не сырой dict)."""
    from core.operator_rules import rules_path
    from core.system_directive_addon import system_directive_addon_path

    p_dir = system_directive_addon_path()
    p_rules = rules_path()

    def line(label: str, path: Path, status: Any) -> str:
        ps = f"<code>{html.escape(str(path))}</code>"
        if status is None:
            return f"• <b>{label}</b> → {ps}\n  не изменён (уже есть содержимое или не запрашивали force)."
        s = str(status)
        if s.startswith("written:"):
            return f"• <b>{label}</b> → {ps}\n  записан из примера."
        if s.startswith("skip_no_example"):
            return f"• <b>{label}</b> → {ps}\n  пропуск: нет файла-примера в <code>config/</code>."
        if s.startswith("error_mkdir") or s.startswith("error_copy"):
            return f"• <b>{label}</b> → {ps}\n  ошибка: <code>{html.escape(s)}</code>"
        return f"• <b>{label}</b> → {ps}\n  <code>{html.escape(s)}</code>"

    parts = [
        line("Директива (addon)", p_dir, report.get("directive")),
        line("operator_rules.json", p_rules, report.get("operator_rules")),
    ]
    d, r = report.get("directive"), report.get("operator_rules")
    if d is None and r is None:
        parts.append(
            "\n<i>Оба файла уже были непустыми. Чтобы перезаписать: "
            "<code>/admin_seed_runtime force</code> или <code>all</code>.</i>"
        )
    return "\n".join(parts)


def seed_runtime_config_on_boot() -> None:
    """Вызывается из ensure_runtime_data_layout: только если RUNTIME_CONFIG_SEED не выключен."""
    if not _truthy("RUNTIME_CONFIG_SEED", True):
        return
    force_dir = _truthy("RUNTIME_DIRECTIVE_SEED_FORCE", False)
    force_rules = _truthy("RUNTIME_OPERATOR_RULES_SEED_FORCE", False)
    seed_runtime_config_from_examples(
        force_directive=force_dir,
        force_operator_rules=force_rules,
    )
