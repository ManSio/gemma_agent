"""
Контракт плагинов и runtime-диагностика конфликтов.

Назначение:
- Сразу при загрузке манифеста ловить «тихие» дефекты:
  * пустые/дублирующиеся команды,
  * конфликт slash-токенов с ядром или другим плагином,
  * пустой список capabilities при type='tool',
  * прописанные buttons без callback_data/text.
- Отдельная функция validate_registry() даёт сводку по всему загруженному
  набору — её показывает /admin_plugins_health и release-guard.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core.command_catalog import (
    CORE_COMMANDS,
    iter_plugin_command_tokens,
    normalize_command_token,
)


@dataclass(frozen=True)
class ManifestIssue:
    severity: str  # 'error' | 'warning' | 'info'
    code: str
    message: str
    plugin: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "plugin": self.plugin,
        }


_CORE_TOKENS = {tok for spec in CORE_COMMANDS for tok in spec.all_tokens()}


def _command_token(c: Any) -> str:
    if isinstance(c, str):
        return normalize_command_token(c if c.startswith("/") else f"/{c}")
    if isinstance(c, dict):
        for key in ("trigger", "command", "name"):
            v = c.get(key)
            if isinstance(v, str) and v.strip():
                return normalize_command_token(v if v.startswith("/") else f"/{v}")
    return ""


def validate_manifest(
    manifest: Any,
    *,
    other_plugin_tokens: Optional[Dict[str, Sequence[str]]] = None,
) -> List[ManifestIssue]:
    """Проверяет одиночный manifest. other_plugin_tokens — для проверки межплагинных конфликтов."""
    issues: List[ManifestIssue] = []
    name = str(getattr(manifest, "name", "") or "")
    if not name:
        issues.append(ManifestIssue("error", "no_name", "manifest.name пустой"))

    mtype = str(getattr(manifest, "type", "") or "")
    _known_types = {
        "tool",
        "model",
        "rag",
        "orchestrator",
        "ui",
        "input",
        "skill",
        "module",
        "memory",
    }
    if mtype not in _known_types:
        issues.append(
            ManifestIssue(
                "warning",
                "unknown_type",
                f"manifest.type='{mtype}' не входит в стандартный набор",
                plugin=name,
            )
        )

    commands = list(getattr(manifest, "commands", []) or [])
    seen_local: set[str] = set()
    for c in commands:
        tok = _command_token(c)
        if not tok:
            issues.append(
                ManifestIssue(
                    "warning",
                    "empty_command",
                    f"Пустая команда в манифесте: {c!r}",
                    plugin=name,
                )
            )
            continue
        if tok in seen_local:
            issues.append(
                ManifestIssue(
                    "warning",
                    "duplicate_command_in_plugin",
                    f"Команда '/{tok}' указана несколько раз внутри плагина",
                    plugin=name,
                )
            )
            continue
        seen_local.add(tok)
        if tok in _CORE_TOKENS:
            issues.append(
                ManifestIssue(
                    "error",
                    "command_collides_with_core",
                    f"Команда '/{tok}' уже принадлежит ядру",
                    plugin=name,
                )
            )
        if other_plugin_tokens:
            for other_name, other_tokens in other_plugin_tokens.items():
                if other_name == name:
                    continue
                if tok in other_tokens:
                    issues.append(
                        ManifestIssue(
                            "error",
                            "command_collides_with_plugin",
                            f"Команда '/{tok}' пересекается с плагином '{other_name}'",
                            plugin=name,
                        )
                    )

    buttons = list(getattr(manifest, "buttons", []) or [])
    for b in buttons:
        if not isinstance(b, dict):
            issues.append(
                ManifestIssue(
                    "warning",
                    "button_not_dict",
                    f"Кнопка должна быть объектом, получили: {b!r}",
                    plugin=name,
                )
            )
            continue
        if not (b.get("text") or b.get("title") or b.get("label")):
            issues.append(
                ManifestIssue(
                    "warning",
                    "button_no_text",
                    f"Кнопка без текста: {b!r}",
                    plugin=name,
                )
            )
        if not (
            b.get("callback_data")
            or b.get("command")
            or b.get("trigger")
            or b.get("simulate_text")
            or b.get("url")
        ):
            issues.append(
                ManifestIssue(
                    "warning",
                    "button_no_action",
                    "Кнопка без action (callback_data/command/trigger/simulate_text/url)",
                    plugin=name,
                )
            )

    capabilities = list(getattr(manifest, "capabilities", []) or [])
    if mtype in {"tool", "skill"} and not commands and not capabilities:
        issues.append(
            ManifestIssue(
                "warning",
                "tool_without_capabilities_or_commands",
                "Плагин типа tool/skill не объявляет ни команды, ни capabilities — "
                "оркестратор не сможет его вызвать",
                plugin=name,
            )
        )
    return issues


def validate_registry(plugin_registry: Any) -> Dict[str, Any]:
    """
    Снимок состояния по всем загруженным плагинам.
    Возвращает структуру с полями:
      total, ok, with_warnings, with_errors,
      issues: List[dict],
      collisions: dict[token]->List[owners]
    """
    loaded = getattr(plugin_registry, "loaded_modules", {}) or {}
    plugin_tokens: Dict[str, List[str]] = {}
    for name, mod in loaded.items():
        manifest = getattr(mod, "manifest", None)
        if not manifest or not hasattr(manifest, "iter_command_tokens"):
            continue
        try:
            plugin_tokens[str(name)] = list(manifest.iter_command_tokens() or [])
        except Exception:
            plugin_tokens[str(name)] = []

    all_issues: List[ManifestIssue] = []
    plugin_summary: Dict[str, Dict[str, Any]] = {}
    for name, mod in loaded.items():
        manifest = getattr(mod, "manifest", None)
        if not manifest:
            continue
        issues = validate_manifest(manifest, other_plugin_tokens=plugin_tokens)
        plugin_summary[str(name)] = {
            "errors": [i.to_dict() for i in issues if i.severity == "error"],
            "warnings": [i.to_dict() for i in issues if i.severity == "warning"],
            "commands": plugin_tokens.get(str(name), []),
        }
        all_issues.extend(issues)

    # Конфликты slash-токенов между плагинами и/или ядром.
    collisions: Dict[str, List[str]] = {}
    for tok in _CORE_TOKENS:
        collisions.setdefault(tok, []).append("core")
    for plugin_name, tokens in plugin_tokens.items():
        for tok in tokens:
            collisions.setdefault(tok, []).append(f"plugin:{plugin_name}")
    collisions = {tok: owners for tok, owners in collisions.items() if len(owners) > 1}

    total = len(plugin_summary)
    with_errors = sum(1 for s in plugin_summary.values() if s["errors"])
    with_warnings = sum(1 for s in plugin_summary.values() if s["warnings"] and not s["errors"])
    ok = total - with_errors - with_warnings

    return {
        "total": total,
        "ok": ok,
        "with_warnings": with_warnings,
        "with_errors": with_errors,
        "issues": [i.to_dict() for i in all_issues],
        "collisions": collisions,
        "plugins": plugin_summary,
    }


__all__ = [
    "ManifestIssue",
    "validate_manifest",
    "validate_registry",
]
