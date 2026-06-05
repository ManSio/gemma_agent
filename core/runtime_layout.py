"""
Создание каталогов данных и выставление прав при старте (Unix).

chown отсюда не делается — нужен root; один раз на сервере: chown -R gemma:gemma /opt/gemma_agent
"""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Iterable, Set

logger = logging.getLogger(__name__)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _base_dir() -> Path:
    pr = os.getenv("PROJECT_ROOT", "").strip()
    if pr:
        return Path(pr).resolve()
    return Path.cwd()


def _resolve_path(raw: str) -> Path:
    p = Path(raw.strip())
    if p.is_absolute():
        return p.resolve()
    return (_base_dir() / p).resolve()


def _collect_dir_paths() -> Set[Path]:
    """Каталоги и родители файловых путей из типичных переменных окружения."""
    dirs: Set[Path] = set()

    def add_dir(d: Path) -> None:
        dirs.add(d)

    env_dirs = [
        ("ERROR_ANALYSIS_DIR", "data"),
        ("RESILIENCE_RUNTIME_DIR", "data/runtime"),
        ("BEHAVIOR_DATA_DIR", "data/users"),
        ("CACHE_PATH", "data/cache"),
        ("AUTONOMY_BACKUP_ROOT", "data/autonomy_backups"),
        ("SITE_RECIPE_DIR", "data/site_recipes"),
        ("SITE_RECIPE_CACHE_DIR", "data/site_recipe_cache"),
        ("LAW_ACT_CACHE_DIR", "data/law_act_cache"),
        ("DOCUMENT_CORPUS_DIR", "data/document_corpus"),
        ("PASSPORT_BACKUP_DIR", "data/passport_backups"),
    ]
    for env, default in env_dirs:
        raw = (os.getenv(env) or default).strip()
        if raw:
            add_dir(_resolve_path(raw))

    env_files = [
        ("RAG_DATABASE_PATH", "data/rag/store.sqlite"),
        ("DATABASE_PATH", "data/database.sqlite"),
        ("DEVELOPMENT_PASSPORT_PATH", "data/development_passport.json"),
    ]
    for env, default in env_files:
        raw = (os.getenv(env) or default).strip()
        if raw:
            add_dir(_resolve_path(raw).parent)

    td = os.getenv("FILE_TEMP_DIR", "").strip()
    if td and td not in ("/tmp", "/var/tmp"):
        add_dir(_resolve_path(td))

    modules = os.getenv("MODULES_PATH", "./modules").strip()
    if modules:
        # под каталогом модулей иногда data (тесты); не трогаем сами modules как 750-only
        pass

    return dirs


def _dir_mode() -> int:
    try:
        return int(os.getenv("RUNTIME_DIR_MODE", "750"), 8)
    except ValueError:
        return 0o750


def _apply_dir_mode(path: Path, mode: int) -> None:
    if os.name == "nt":
        return
    if not _truthy("RUNTIME_APPLY_DIR_MODE", True):
        return
    try:
        os.chmod(path, mode)
    except (OSError, PermissionError) as e:
        logger.debug("runtime_layout: chmod %s: %s", path, e)


def _warn_env_permissions() -> None:
    if os.name == "nt":
        return
    env_file = _base_dir() / ".env"
    if not env_file.is_file():
        return
    try:
        mode = stat.S_IMODE(env_file.stat().st_mode)
    except OSError:
        return
    if mode & stat.S_IRGRP or mode & stat.S_IROTH:
        logger.warning(
            "runtime_layout: .env доступен группе/остальным (mode %o). Рекомендуется chmod 600.",
            mode,
        )


def ensure_runtime_data_layout() -> None:
    """
    Создаёт недостающие каталоги данных и выставляет RUNTIME_DIR_MODE (по умолчанию 0750).
    Отключить: RUNTIME_ENSURE_DATA_LAYOUT=false
    """
    if not _truthy("RUNTIME_ENSURE_DATA_LAYOUT", True):
        return
    mode = _dir_mode()
    created = 0
    for d in sorted(_collect_dir_paths(), key=lambda p: str(p)):
        try:
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                created += 1
            elif d.is_file():
                logger.warning("runtime_layout: ожидался каталог, это файл: %s", d)
                continue
            _apply_dir_mode(d, mode)
        except OSError as e:
            logger.error("runtime_layout: не удалось подготовить %s: %s", d, e)
            raise
    if created:
        logger.info("runtime_layout: создано каталогов: %s", created)
    _warn_env_permissions()
    try:
        from core.development_passport import ensure_default_passport_file

        ensure_default_passport_file()
    except Exception as e:
        logger.warning("runtime_layout: паспорт по умолчанию не создан: %s", e)
    try:
        from core.runtime_config_seed import seed_runtime_config_on_boot

        seed_runtime_config_on_boot()
    except Exception as e:
        logger.warning("runtime_layout: runtime_config_seed: %s", e)
