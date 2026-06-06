"""
Self-Code Evolution (Фаза 9) — система авто-изменения кода.

9.1 Patch Runner — генерирует Python-патч (diff), применяет, тестирует, коммитит, деплоит
9.2 Auto-Optimizer — замечает тормозящие функции (p95), предлагает рефакторинг
9.3 Evolution Log — журнал всех изменений кода

Безопасность:
- Все патчи сначала проверяются: py_compile → тесты (если есть) → фиксация
- Разрешённые директории: core/ (основной код)
- Каждый патч имеет undo-запись через EvolutionLog
"""
from __future__ import annotations

import ast
import difflib
import json
import logging
import os
import re
import subprocess  # noqa: S404 — только для тестов/деплоя
import sys
import textwrap
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ─── Конфигурация ────────────────────────────────────────────────────────

_CODE_EVOL_ENABLED = os.getenv("CODE_EVOL_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
_CODE_EVOL_ALLOWED_DIRS: Set[str] = set(
    d.strip() for d in os.getenv("CODE_EVOL_ALLOWED_DIRS", "core").split(",") if d.strip()
)
_CODE_EVOL_PATCHES_MAX = int(os.getenv("CODE_EVOL_PATCHES_MAX", "50"))
_CODE_EVOL_DEPLOY_SSH = os.getenv("CODE_EVOL_DEPLOY_SSH", "").strip()
_CODE_EVOL_DEPLOY_CMD = os.getenv("CODE_EVOL_DEPLOY_CMD", "bash /opt/gemma_agent/scripts/gemma_panel.sh update").strip()
_CODE_EVOL_GIT_USER = os.getenv("CODE_EVOL_GIT_USER", "gemma-bot").strip()
_CODE_EVOL_GIT_EMAIL = os.getenv("CODE_EVOL_GIT_EMAIL", "bot@localhost").strip()
_CODE_EVOL_OPTIMIZER_INTERVAL = int(os.getenv("CODE_EVOL_OPTIMIZER_INTERVAL", "720"))
_CODE_EVOL_LLM_MODEL = os.getenv("CODE_EVOL_LLM_MODEL", "deepseek/deepseek-v4-pro").strip()

# ─── Data Classes ─────────────────────────────────────────────────────────

@dataclass
class CodePatch:
    """
    Один патч — diff, который можно применить к коду.

    generated_by: "auto_optimizer" | "mce" | "manual"
    """
    id: str
    ts: float
    generated_by: str
    target_file: str
    diff_text: str
    description: str
    reason: str
    metric_before: Dict[str, float] = field(default_factory=dict)
    status: str = "pending"
    test_result: str = ""
    test_ok: bool = False
    commit_sha: str = ""
    deploy_ok: bool = False
    rolled_back_at: float = 0.0
    metric_after: Dict[str, float] = field(default_factory=dict)


@dataclass
class OptimizationTarget:
    """Цель для авто-оптимизатора: файл и функция, которые тормозят."""
    file_path: str
    func_name: str
    p95_ms: float
    call_count: int
    suggested_refactor: str = ""


# ─── EvolutionLog ─────────────────────────────────────────────────────────

class EvolutionLog:
    """Журнал изменений кода — JSONL."""

    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []
        self._lock = Lock()

    def record(self, event_type: str, details: Dict[str, Any]) -> None:
        entry = {
            "id": uuid.uuid4().hex[:12],
            "ts": time.time(),
            "event_type": event_type,
            "details": details,
        }
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > _CODE_EVOL_PATCHES_MAX:
                self._entries = self._entries[-_CODE_EVOL_PATCHES_MAX:]
        try:
            path = self._log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.debug("[code_evol] log write: %s", e)

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            entries = list(self._entries)
        entries.sort(key=lambda e: e["ts"], reverse=True)
        return entries[:limit]

    @staticmethod
    def _log_path() -> Path:
        p = Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))
        return p.resolve() / "code_evolution_log.jsonl"


# ─── PatchRunner ──────────────────────────────────────────────────────────

class PatchRunner:
    """
    Генерация, применение, тестирование и деплой патчей.

    Поток:
    1. generate_diff(source_code, fix_description) -> diff_text
    2. apply_patch(diff_text, target_file) -> ok/error
    3. run_tests(target_file) -> exit_code, stdout
    4. commit_and_deploy(patch) -> commit_sha, deploy_ok
    5. rollback(patch) -> откат через git checkout
    """

    def __init__(self, evol_log: EvolutionLog) -> None:
        self._log = evol_log
        self._patches: List[CodePatch] = []
        self._lock = Lock()

    # ─── 9.1: Генерация патча ───────────────────────────────────────────

    def generate_patch(
        self,
        source_code: str,
        fix_description: str,
        target_file: str,
        *,
        generated_by: str = "auto_optimizer",
        reason: str = "",
        metric_before: Optional[Dict[str, float]] = None,
    ) -> Optional[CodePatch]:
        if not self._is_allowed(target_file):
            logger.info("[code_evol] patch rejected: %s not in allowed dirs", target_file)
            return None

        filepath = self._resolve_path(target_file)
        if not filepath:
            logger.info("[code_evol] patch rejected: %s not found", target_file)
            return None

        original = filepath.read_text(encoding="utf-8", errors="replace")
        if original == source_code:
            logger.info("[code_evol] no changes for %s", target_file)
            return None

        try:
            ast.parse(source_code)
        except SyntaxError as e:
            logger.warning("[code_evol] syntax error in new code: %s", e)
            return None

        orig_lines = original.splitlines(keepends=True)
        new_lines = source_code.splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(
            orig_lines, new_lines,
            fromfile=f"a/{target_file}",
            tofile=f"b/{target_file}",
            fromfiledate="",
            tofiledate="",
            n=3,
        ))

        if not diff.strip():
            logger.info("[code_evol] empty diff for %s", target_file)
            return None

        patch = CodePatch(
            id=uuid.uuid4().hex[:12],
            ts=time.time(),
            generated_by=generated_by,
            target_file=target_file,
            diff_text=diff,
            description=fix_description,
            reason=reason or fix_description,
            metric_before=metric_before or {},
        )
        with self._lock:
            self._patches.append(patch)
            if len(self._patches) > _CODE_EVOL_PATCHES_MAX:
                self._patches = self._patches[-_CODE_EVOL_PATCHES_MAX:]
        self._log.record("patch_generated", {
            "patch_id": patch.id,
            "target": target_file,
            "description": fix_description,
            "diff_size": len(diff),
        })
        logger.info("[code_evol] patch %s generated for %s (%d lines diff)",
                     patch.id, target_file, len(diff.splitlines()))
        return patch

    @staticmethod
    def _ast_optimize(tree: ast.AST, source: str) -> str:
        """
        AST-оптимизация: находит и исправляет реальные анти-паттерны.

        Оптимизации:
        1. time.sleep(N) -> await asyncio.sleep(N) внутри async def
        2. bare except: -> except Exception:
        """
        lines = source.splitlines(keepends=True)
        modified = False
        replacements: Dict[int, str] = {}

        # 1. time.sleep -> await asyncio.sleep внутри async def
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
                continue
            if func.value.id != "time" or func.attr != "sleep":
                continue
            if node.lineno is None:
                continue
            inside_async = False
            for parent in ast.walk(tree):
                if isinstance(parent, ast.AsyncFunctionDef):
                    p_start = parent.lineno or 0
                    p_end = parent.end_lineno or 0
                    if p_start <= node.lineno <= p_end:
                        inside_async = True
                        break
            if not inside_async:
                continue
            old_line = lines[node.lineno - 1]
            new_line = old_line.replace("time.sleep(", "await asyncio.sleep(")
            if new_line != old_line:
                replacements[node.lineno] = new_line
                modified = True

        # 2. bare except: -> except Exception:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if node.type is not None:
                continue
            if node.lineno is None:
                continue
            old_line = lines[node.lineno - 1]
            indent = len(old_line) - len(old_line.lstrip())
            new_line = " " * indent + "except Exception:\n"
            if new_line != old_line:
                replacements[node.lineno] = new_line
                modified = True

        if not modified:
            return source

        for lineno in sorted(replacements, reverse=True):
            if 1 <= lineno <= len(lines):
                lines[lineno - 1] = replacements[lineno]

        result = "".join(lines)
        try:
            ast.parse(result)
            return result
        except SyntaxError:
            return source

    # ─── Применение патча ───────────────────────────────────────────────

    def apply_patch(self, patch: CodePatch) -> bool:
        if patch.status != "pending":
            logger.info("[code_evol] patch %s already %s", patch.id, patch.status)
            return False

        filepath = self._resolve_path(patch.target_file)
        if not filepath:
            self._fail_patch(patch, "file not found")
            return False

        backup = filepath.read_text(encoding="utf-8", errors="replace")

        try:
            if patch.diff_text.startswith("---"):
                new_code = self._apply_unified_diff(backup, patch.diff_text)
            else:
                new_code = patch.diff_text

            try:
                ast.parse(new_code)
            except SyntaxError as e:
                self._fail_patch(patch, f"syntax error: {e}")
                return False

            filepath.write_text(new_code, encoding="utf-8")
            patch.status = "testing"
            self._log.record("patch_applied", {
                "patch_id": patch.id,
                "target": patch.target_file,
            })
            logger.info("[code_evol] patch %s applied to %s", patch.id, patch.target_file)
            return True

        except Exception as e:
            try:
                filepath.write_text(backup, encoding="utf-8")
            except Exception as e:
                logger.debug('%s optional failed: %s', 'code_evolution', e, exc_info=True)
            self._fail_patch(patch, str(e))
            return False

    @staticmethod
    def _apply_unified_diff(original: str, diff_text: str) -> str:
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as orig_f:
                orig_f.write(original)
                orig_path = orig_f.name

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".diff", delete=False, encoding="utf-8"
            ) as diff_f:
                diff_f.write(diff_text)
                diff_path = diff_f.name

            try:
                result = subprocess.run(
                    ["patch", "--force", "--no-backup-if-mismatch", orig_path, diff_path],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0:
                    patched = Path(orig_path).read_text(encoding="utf-8")
                    return patched
                else:
                    logger.warning("[code_evol] patch apply failed: %s", result.stderr[:200])
                    return original
            finally:
                try:
                    Path(orig_path).unlink(missing_ok=True)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'code_evolution', e, exc_info=True)
                try:
                    Path(diff_path).unlink(missing_ok=True)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'code_evolution', e, exc_info=True)
        except Exception as e:
            logger.warning("[code_evol] _apply_unified_diff error: %s", e)
            return original

    # ─── Тестирование ───────────────────────────────────────────────────

    def run_tests(self, patch: CodePatch, timeout_sec: int = 60) -> bool:
        if patch.status not in ("testing", "applied"):
            return False

        test_file = self._find_test_file(patch.target_file)
        if not test_file:
            test_file = patch.target_file
            cmd = [sys.executable, "-m", "py_compile", test_file]
        else:
            cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", test_file]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                cwd=self._project_root(),
            )
            patch.test_result = result.stdout + result.stderr
            patch.test_ok = result.returncode == 0

            if patch.test_ok:
                patch.status = "applied"
            else:
                patch.status = "failed"

            self._log.record("patch_tested", {
                "patch_id": patch.id,
                "test_ok": patch.test_ok,
                "test_file": test_file or "",
            })
            return patch.test_ok

        except subprocess.TimeoutExpired:
            patch.test_result = f"TIMEOUT ({timeout_sec}s)"
            patch.status = "failed"
            return False
        except Exception as e:
            patch.test_result = str(e)
            patch.status = "failed"
            return False

    # ─── Коммит и деплой ────────────────────────────────────────────────

    def commit_and_deploy(self, patch: CodePatch) -> bool:
        if patch.status != "applied" or not patch.test_ok:
            logger.info("[code_evol] patch %s not ready for deploy", patch.id)
            return False

        root = self._project_root()
        try:
            subprocess.run(
                ["git", "add", patch.target_file],
                cwd=root, capture_output=True, text=True, timeout=30,
            )

            commit_msg = f"auto(code-evol): {patch.description[:80]}"
            result = subprocess.run(
                ["git", "commit", "-m", commit_msg,
                 "--author", f"{_CODE_EVOL_GIT_USER} <{_CODE_EVOL_GIT_EMAIL}>"],
                cwd=root, capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                patch.status = "failed"
                patch.test_result += result.stdout + result.stderr
                return False

            sha_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root, capture_output=True, text=True, timeout=10,
            )
            patch.commit_sha = sha_result.stdout.strip()

            push = subprocess.run(
                ["git", "push"],
                cwd=root, capture_output=True, text=True, timeout=60,
            )
            if push.returncode != 0:
                logger.warning("[code_evol] push warning: %s", push.stderr[:200])

            deploy_ok = self._trigger_deploy(patch)
            patch.deploy_ok = deploy_ok

            self._log.record("patch_deployed", {
                "patch_id": patch.id,
                "commit_sha": patch.commit_sha,
                "deploy_ok": deploy_ok,
            })
            return True

        except subprocess.TimeoutExpired as e:
            logger.warning("[code_evol] git timeout: %s", e)
            patch.status = "failed"
            return False
        except Exception as e:
            logger.warning("[code_evol] commit error: %s", e)
            patch.status = "failed"
            return False

    def _trigger_deploy(self, patch: CodePatch) -> bool:
        if _CODE_EVOL_DEPLOY_SSH:
            cmd_parts = ["ssh", "-o", "ConnectTimeout=10", _CODE_EVOL_DEPLOY_SSH, _CODE_EVOL_DEPLOY_CMD]
        else:
            cmd_parts = ["bash", "-c", _CODE_EVOL_DEPLOY_CMD]

        try:
            result = subprocess.run(
                cmd_parts,
                capture_output=True, text=True, timeout=120,
                cwd=self._project_root(),
            )
            ok = result.returncode == 0
            patch.deploy_ok = ok
            if not ok:
                logger.warning("[code_evol] deploy failed: %s", result.stderr[:300])
            return ok
        except Exception as e:
            logger.warning("[code_evol] deploy error: %s", e)
            return False

    # ─── Откат ──────────────────────────────────────────────────────────

    def rollback(self, patch_id: str) -> bool:
        patch = self._find_patch(patch_id)
        if not patch:
            return False

        root = self._project_root()
        try:
            result = subprocess.run(
                ["git", "checkout", "HEAD~1", "--", patch.target_file],
                cwd=root, capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                patch.status = "rolled_back"
                patch.rolled_back_at = time.time()
                self._log.record("patch_rolled_back", {
                    "patch_id": patch.id,
                    "target": patch.target_file,
                })
                return True
            return False
        except Exception as e:
            logger.warning("[code_evol] rollback error: %s", e)
            return False

    # ─── Хелперы ────────────────────────────────────────────────────────

    def list_patches(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            patches = list(self._patches)
        patches.sort(key=lambda p: p.ts, reverse=True)
        return [asdict(p) for p in patches[:limit]]

    def _find_patch(self, patch_id: str) -> Optional[CodePatch]:
        with self._lock:
            for p in self._patches:
                if p.id == patch_id:
                    return p
        return None

    def _fail_patch(self, patch: CodePatch, reason: str) -> None:
        patch.status = "failed"
        patch.test_result = reason
        self._log.record("patch_failed", {
            "patch_id": patch.id,
            "reason": reason,
        })

    @staticmethod
    def _is_allowed(target_file: str) -> bool:
        for d in _CODE_EVOL_ALLOWED_DIRS:
            if target_file.startswith(d + "/") or target_file.startswith(d + "\\"):
                return True
        return False

    @staticmethod
    def _resolve_path(target_file: str) -> Optional[Path]:
        root = Path(_project_root())
        full = (root / target_file).resolve()
        try:
            full.relative_to(root.resolve())
        except ValueError:
            return None
        if not full.exists():
            return None
        return full

    @staticmethod
    def _find_test_file(target_file: str) -> Optional[str]:
        stem = Path(target_file).stem
        test_candidates = [
            f"tests/test_{stem}.py",
            f"tests/{stem}_test.py",
        ]
        root = _project_root()
        for tc in test_candidates:
            if (Path(root) / tc).exists():
                return tc
        return None

    @staticmethod
    def _project_root() -> str:
        return os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()


# ─── AutoOptimizer ────────────────────────────────────────────────────────

class AutoOptimizer:
    """
    9.2 — Авто-оптимизатор.

    Анализирует p95 из Observability, находит тормозящие функции
    через AST-анализ + LLM, генерирует патчи через PatchRunner.
    """

    def __init__(self, patch_runner: PatchRunner, evol_log: EvolutionLog) -> None:
        self._runner = patch_runner
        self._log = evol_log
        self._tick_counter = 0

    def tick(self) -> None:
        if not _CODE_EVOL_ENABLED:
            return
        self._tick_counter += 1
        if self._tick_counter % _CODE_EVOL_OPTIMIZER_INTERVAL != 0:
            return

        try:
            targets = self._find_slow_functions()
            if not targets:
                return
            for target in targets[:1]:
                self._optimize_target(target)
        except Exception as e:
            logger.warning("[code_evol] optimizer tick error: %s", e)

    def _find_slow_functions(self) -> List[OptimizationTarget]:
        targets: List[OptimizationTarget] = []

        try:
            from core.observability import OBS
            snap = OBS.snapshot()
            latencies = snap.get("latency_p95_ms", {})

            for key, p95_val in latencies.items():
                if not isinstance(p95_val, (int, float)) or p95_val < 5000:
                    continue
                parts = key.split("_")
                if len(parts) >= 2 and parts[0] in _CODE_EVOL_ALLOWED_DIRS:
                    file_path = f"{parts[0]}/{'/'.join(parts[1:])}.py"
                else:
                    file_path = f"core/{parts[-1]}.py" if parts else "core/unknown.py"

                targets.append(OptimizationTarget(
                    file_path=file_path,
                    func_name=key,
                    p95_ms=float(p95_val),
                    call_count=int(snap.get("counters", {}).get(f"{key}_count", 0)),
                    suggested_refactor=f"p95={p95_val:.0f}ms — оптимизировать {key}",
                ))
        except Exception as e:
            logger.debug('%s optional failed: %s', 'code_evolution', e, exc_info=True)
        if not targets:
            targets = self._find_slow_by_error_rate()

        return targets

    @staticmethod
    def _find_slow_by_error_rate() -> List[OptimizationTarget]:
        try:
            from core.monitoring import MONITOR
            fail_count = int(MONITOR.counters.get("module_exec_fail_total", 0))
            ok_count = int(MONITOR.counters.get("module_exec_ok_total", 1))
            if ok_count > 0 and fail_count / ok_count > 0.2:
                return [
                    OptimizationTarget(
                        file_path="core/heal_executor.py",
                        func_name="apply_steps",
                        p95_ms=5000.0,
                        call_count=fail_count + ok_count,
                        suggested_refactor=f"fail_rate={fail_count}/{ok_count}",
                    )
                ]
        except Exception as e:
            logger.debug('%s optional failed: %s', 'code_evolution', e, exc_info=True)
        return []

    def _optimize_target(self, target: OptimizationTarget) -> None:
        filepath = PatchRunner._resolve_path(target.file_path)
        if not filepath:
            self._log.record("optimizer_skip", {
                "target": target.file_path,
                "reason": "file not found",
            })
            return

        source = filepath.read_text(encoding="utf-8", errors="replace")

        func_code = self._find_slow_function_ast(source, target.func_name)
        if not func_code:
            self._log.record("optimizer_skip", {
                "target": target.file_path,
                "reason": f"function {target.func_name} not found",
            })
            return

        # Сначала пробуем LLM-генерацию, при ошибке — AST-оптимизация
        optimized = self._generate_optimization(source, func_code, target)
        if not optimized or optimized == source:
            return

        patch = self._runner.generate_patch(
            source_code=optimized,
            fix_description=target.suggested_refactor,
            target_file=target.file_path,
            generated_by="auto_optimizer",
            reason=f"p95={target.p95_ms:.0f}ms, calls={target.call_count}",
            metric_before={"p95": target.p95_ms},
        )
        if not patch:
            return

        if not self._runner.apply_patch(patch):
            return

        if not self._runner.run_tests(patch):
            self._runner.rollback(patch.id)
            return

        self._runner.commit_and_deploy(patch)

        self._log.record("optimizer_completed", {
            "patch_id": patch.id,
            "target": target.file_path,
            "commit_sha": patch.commit_sha,
            "deploy_ok": patch.deploy_ok,
        })

    @staticmethod
    def _find_slow_function_ast(source: str, func_name: str) -> Optional[str]:
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                    return ast.get_source_segment(source, node) or ""
        except SyntaxError:
            pass
        return None

    @staticmethod
    async def _generate_llm_optimization(source: str, func_code: str, target: OptimizationTarget) -> Optional[str]:
        """
        Сгенерировать оптимизированную версию через LLM.

        Формирует промпт с контекстом и вызывает OpenRouterProvider.
        При ошибке возвращает None — вызывающий падает на AST fallback.
        """
        try:
            from core.openrouter_provider import get_openrouter_provider

            system_prompt = (
                "Ты — эксперт по оптимизации Python-кода. "
                "Тебе дана функция с высокой latency. "
                "Перепиши ТОЛЬКО эту функцию, оптимизируя её. "
                "Сохрани ту же сигнатуру (имя, аргументы). "
                "Ответь ТОЛЬКО исходным кодом функции, без пояснений, без ```python."
            )

            user_prompt = (
                f"Оптимизируй функцию `{target.func_name}` "
                f"(p95={target.p95_ms:.0f}ms, {target.call_count} вызовов):\n\n"
                f"```python\n{func_code}\n```\n\n"
                f"Оптимизируй: мемоизация, asyncio.gather, batch-запросы, "
                f"early return, кеширование, замена sync на async где уместно."
            )

            provider = get_openrouter_provider()
            resp = await provider.generate(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=_CODE_EVOL_LLM_MODEL,
                temperature=0.2,
                max_tokens=1500,
            )
            llm_result = resp.get("content", "").strip()

            # Очищаем от возможных markdown-обёрток
            llm_result = llm_result.strip()
            if llm_result.startswith("```"):
                llm_result = llm_result.split("\n", 1)[1] if "\n" in llm_result else llm_result
            if llm_result.endswith("```"):
                llm_result = llm_result.rsplit("```", 1)[0]
            llm_result = llm_result.strip()

            if not llm_result:
                return None

            # Проверяем, что результат — валидный Python
            try:
                ast.parse(llm_result)
            except SyntaxError:
                logger.warning("[code_evol] LLM generated invalid syntax, falling back to AST")
                return None

            # Заменяем исходную функцию на оптимизированную
            optimized = source.replace(func_code, llm_result)
            if optimized == source:
                return None

            # Проверяем итоговый синтаксис
            try:
                ast.parse(optimized)
            except SyntaxError:
                logger.warning("[code_evol] LLM result breaks file syntax, falling back")
                return None

            return optimized

        except Exception as e:
            logger.debug("[code_evol] LLM optimization error: %s", e)
            return None

    def _generate_optimization(self, source: str, func_code: str, target: OptimizationTarget) -> str:
        """
        Сгенерировать оптимизированную версию.

        Сначала пробует LLM-генерацию, при ошибке — AST-трансформации.
        """
        # Пробуем LLM (если event loop не занят)
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if not loop.is_running():
                llm_opt = loop.run_until_complete(
                    self._generate_llm_optimization(source, func_code, target)
                )
                if llm_opt:
                    return llm_opt
        except Exception as e:
            logger.debug('%s optional failed: %s', 'code_evolution', e, exc_info=True)
        # Fallback: AST-трансформации
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        optimized = PatchRunner._ast_optimize(tree, source)
        return optimized


# ─── Глобальный доступ ───────────────────────────────────────────────────

_EVOL_LOG: Optional[EvolutionLog] = None
_PATCH_RUNNER: Optional[PatchRunner] = None
_AUTO_OPTIMIZER: Optional[AutoOptimizer] = None


def get_evolution_log() -> EvolutionLog:
    global _EVOL_LOG
    if _EVOL_LOG is None:
        _EVOL_LOG = EvolutionLog()
    return _EVOL_LOG


def get_patch_runner() -> PatchRunner:
    global _PATCH_RUNNER
    if _PATCH_RUNNER is None:
        _PATCH_RUNNER = PatchRunner(get_evolution_log())
    return _PATCH_RUNNER


def get_auto_optimizer() -> AutoOptimizer:
    global _AUTO_OPTIMIZER
    if _AUTO_OPTIMIZER is None:
        _AUTO_OPTIMIZER = AutoOptimizer(get_patch_runner(), get_evolution_log())
    return _AUTO_OPTIMIZER


def _project_root() -> str:
    return os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()


__all__ = [
    "CodePatch",
    "OptimizationTarget",
    "EvolutionLog",
    "PatchRunner",
    "AutoOptimizer",
    "get_evolution_log",
    "get_patch_runner",
    "get_auto_optimizer",
]
