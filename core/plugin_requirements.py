from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Политика платформы: pip-пакеты плагинов ставятся только на этапе сборки образа / CI.
INSTALL_POLICY = "build_time_only"
INSTALL_POLICY_DETAIL = (
    "Зависимости из module.json -> pip_requirements не устанавливаются в рантайме. "
    "Добавьте пакеты в манифест и пересоберите образ: "
    "python scripts/merge_plugin_requirements.py --install"
)


def runtime_pip_install_forbidden() -> bool:
    """Явный флаг политики (документация / проверки)."""
    return True


def iter_plugin_manifest_roots() -> List[Path]:
    """Корни каталогов с плагинами/библиотеками, в каждом ожидаются подпапки с module.json."""
    raw = os.getenv("PLUGIN_MANIFEST_PATHS", "").strip()
    if raw:
        return [Path(p.strip()) for p in raw.split(",") if p.strip()]
    roots = [
        Path(os.getenv("MODULES_PATH", "modules")),
        Path(os.getenv("CORE_LIBRARIES_PATH", "core_libraries")),
    ]
    return roots


def collect_plugin_pip_requirements(roots: Optional[Sequence[Path]] = None) -> Dict[str, List[str]]:
    """
    Собирает pip_requirements из всех module.json под указанными корнями.
    Ключ — логический id вида ``modules/echo`` или ``core_libraries/foo``.
    """
    roots = list(roots) if roots is not None else iter_plugin_manifest_roots()
    out: Dict[str, List[str]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        prefix = root.name
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            mj = d / "module.json"
            if not mj.is_file():
                continue
            try:
                data = json.loads(mj.read_text(encoding="utf-8"))
            except Exception as e:
                logger.debug("skip %s: %s", mj, e)
                continue
            reqs = data.get("pip_requirements") or []
            if not isinstance(reqs, list) or not reqs:
                continue
            mod_name = str(data.get("name") or d.name)
            logical = f"{prefix}/{mod_name}"
            cleaned = [str(x).strip() for x in reqs if str(x).strip()]
            if cleaned:
                out[logical] = cleaned
    return out


def collect_modules_pip_requirements(modules_path: str | Path = "modules") -> Dict[str, List[str]]:
    """Только один корень (обратная совместимость). Ключи: ``<имя_каталога>/<name>``."""
    root = Path(modules_path)
    prefix = root.name
    out: Dict[str, List[str]] = {}
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        mj = d / "module.json"
        if not mj.is_file():
            continue
        try:
            data = json.loads(mj.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("skip %s: %s", mj, e)
            continue
        reqs = data.get("pip_requirements") or []
        if not isinstance(reqs, list) or not reqs:
            continue
        name = str(data.get("name") or d.name)
        cleaned = [str(x).strip() for x in reqs if str(x).strip()]
        if cleaned:
            out[f"{prefix}/{name}"] = cleaned
    return out


_VCS_OR_URL_PREFIX = re.compile(r"^[\s]*(-e\s+)?((git|hg|svn)\+|https?://|file:)", re.I)


def requirement_distribution_key(line: str) -> str:
    """
    Ключ для дедупликации/конфликтов: нормализованное имя дистрибутива или вся строка для URL/VCS.
    """
    s = line.strip()
    if not s or s.startswith("#"):
        return ""
    if _VCS_OR_URL_PREFIX.search(s):
        return s
    base = s.split(";", 1)[0].strip()
    base = base.split("[", 1)[0].strip()
    for sep in (" @ ", "===", "==", "!=", "~=", ">=", "<=", ">", "<"):
        if sep in base:
            base = base.split(sep, 1)[0].strip()
            break
    return base.lower().replace("_", "-") if base else s


@dataclass
class PluginPipMergeReport:
    merged_lines: List[str]
    by_module: Dict[str, List[str]]
    duplicate_distribution_keys: List[Dict[str, Any]] = field(default_factory=list)
    hints: List[str] = field(default_factory=list)
    install_policy: str = INSTALL_POLICY
    install_policy_detail: str = INSTALL_POLICY_DETAIL


def merge_plugin_requirements_report(roots: Optional[Sequence[Path]] = None) -> PluginPipMergeReport:
    by_module = collect_plugin_pip_requirements(roots)
    merged: List[str] = []
    seen_lines: set[str] = set()
    key_to_line: Dict[str, str] = {}
    key_to_modules: Dict[str, List[str]] = {}
    conflicts: List[Dict[str, Any]] = []
    hints: List[str] = []

    for mod, reqs in sorted(by_module.items()):
        for r in reqs:
            if r in seen_lines:
                continue
            key = requirement_distribution_key(r)
            if not key:
                continue
            if key not in key_to_line:
                key_to_line[key] = r
                key_to_modules[key] = [mod]
                merged.append(r)
                seen_lines.add(r)
            elif key_to_line[key] == r:
                key_to_modules[key].append(mod)
            else:
                conflicts.append(
                    {
                        "distribution_key": key,
                        "chosen_line": key_to_line[key],
                        "chosen_from": key_to_modules[key],
                        "skipped_line": r,
                        "skipped_from": mod,
                    }
                )
                hints.append(
                    f"Конфликт версий для «{key}»: оставлено «{key_to_line[key]}» "
                    f"({', '.join(key_to_modules[key])}); также «{r}» ({mod}). "
                    "Унифицируйте строку в манифестах и пересоберите образ."
                )

    if conflicts:
        hints.insert(
            0,
            f"Обнаружено конфликтующих спецификаций: {len(conflicts)}. "
            "Проверьте duplicate_distribution_keys и приведите pip_requirements к одной строке на пакет.",
        )

    return PluginPipMergeReport(
        merged_lines=merged,
        by_module=by_module,
        duplicate_distribution_keys=conflicts,
        hints=hints,
    )


def merged_pip_requirements(modules_path: str | Path | None = None) -> List[str]:
    if modules_path is None:
        return merge_plugin_requirements_report().merged_lines
    return merge_plugin_requirements_report(roots=[Path(modules_path)]).merged_lines


def write_plugin_pip_sidecar(module_dir: Path, pip_requirements: Optional[List[str]] = None) -> None:
    """Файл-подсказка рядом с плагином; канонический список — только module.json -> pip_requirements."""
    req_plugin = module_dir / "requirements-plugin.txt"
    pr = [str(x).strip() for x in (pip_requirements or []) if str(x).strip()]
    lines = [
        "# Подсказка для разработчика. Источник истины: module.json -> pip_requirements.",
        "# Рантайм не выполняет pip install. После изменений пересоберите образ:",
        "#   python scripts/merge_plugin_requirements.py --install",
        "",
    ]
    lines.extend(pr)
    req_plugin.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_merged_requirements_file(
    path: str | Path,
    roots: Optional[Sequence[Path]] = None,
    report: Optional[PluginPipMergeReport] = None,
) -> PluginPipMergeReport:
    """Записывает объединённый список в файл (для Docker/диффов)."""
    report = report or merge_plugin_requirements_report(roots)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Автогенерация: plugin pip requirements\n"
        f"# Политика: {INSTALL_POLICY} — см. core/plugin_requirements.py\n"
        f"# Не редактируйте вручную; правьте module.json и перезапустите merge-скрипт.\n"
    )
    body = "\n".join(report.merged_lines)
    p.write_text(header + "\n" + body + ("\n" if body else ""), encoding="utf-8")
    return report
