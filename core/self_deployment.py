"""
Self-Deployment Pipeline: генерация → тест → git → редеплой.
Подхватывается core.tools как SelfDeployment.*.
Включается через SELF_DEPLOY_ENABLED=true.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _module_test_path(module_name: str) -> Path:
    return Path("modules", module_name, "tests.py")


class SelfDeploymentModule:
    async def deploy_module(self, module_name: str) -> Dict[str, Any]:
        if not _env_truthy("SELF_DEPLOY_ENABLED"):
            return {"ok": False, "error": "SELF_DEPLOY_ENABLED is off"}

        result = {"module_name": module_name, "steps": {}}

        tests_passed = await self._run_tests(module_name)
        result["steps"]["tests"] = tests_passed

        if not tests_passed.get("ok"):
            result["ok"] = False
            result["error"] = f"tests failed: {tests_passed.get('error')}"
            logger.warning("[self_deploy] tests failed for %s, skipping git/deploy", module_name)
            return result

        git_result = await self._git_commit_push(module_name)
        result["steps"]["git"] = git_result

        if not git_result.get("ok"):
            result["ok"] = False
            result["error"] = f"git push failed: {git_result.get('error')}"
            logger.warning("[self_deploy] git push failed for %s", module_name)
            return result

        deploy_result = await self._ssh_deploy(module_name)
        result["steps"]["deploy"] = deploy_result

        if deploy_result.get("ok"):
            result["ok"] = True
            result["message"] = f"Module {module_name} deployed successfully"
        else:
            result["ok"] = bool(git_result.get("ok"))
            result["error"] = deploy_result.get("error", "remote deploy failed")

        return result

    async def _run_tests(self, module_name: str) -> Dict[str, Any]:
        test_file = _module_test_path(module_name)
        if not test_file.is_file():
            logger.info("[self_deploy] no tests.py for %s, smoke import only", module_name)
            return await self._smoke_import(module_name)

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m", "pytest",
                str(test_file),
                "-q", "--tb=short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            ok = proc.returncode == 0
            return {
                "ok": ok,
                "returncode": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace")[:2000],
                "stderr": stderr.decode("utf-8", errors="replace")[:1000] if not ok else "",
            }
        except asyncio.TimeoutError:
            return {"ok": False, "error": "pytest timed out after 120s"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def _smoke_import(self, module_name: str) -> Dict[str, Any]:
        module_py = Path("modules", module_name, "module.py")
        if not module_py.is_file():
            return {"ok": False, "error": f"module.py not found: {module_py}"}
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                f"modules.{module_name}.module",
                str(module_py),
            )
            if spec is None or spec.loader is None:
                return {"ok": False, "error": "import spec failed"}
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return {"ok": True, "type": "smoke_import"}
        except Exception as e:
            return {"ok": False, "error": f"smoke import failed: {e}"}

    async def _git_commit_push(self, module_name: str) -> Dict[str, Any]:
        if not _env_truthy("SELF_DEPLOY_GIT_ENABLED", default=True):
            return {"ok": False, "error": "SELF_DEPLOY_GIT_ENABLED is off"}

        module_dir = f"modules/{module_name}"
        branch = _env_str("SELF_DEPLOY_BRANCH", "main")
        git_user = _env_str("SELF_DEPLOY_GIT_USER_NAME", "GemmaAgent")
        git_email = _env_str("SELF_DEPLOY_GIT_USER_EMAIL", "bot@example.com")

        async def _run(cmd: list[str]) -> tuple[int, str, str]:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            return proc.returncode, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")

        # Check for unstaged changes that aren't ours
        rc, out, err = await _run(["git", "status", "--porcelain", "--", "modules/"])
        if rc != 0:
            return {"ok": False, "error": f"git status failed: {err}"}
        other_changes = [line for line in out.splitlines() if module_dir not in line]
        if other_changes:
            return {
                "ok": False,
                "error": f"other uncommitted changes in modules/: {other_changes[:3]!r}",
            }

        # git config (needed for commit in containers)
        await _run(["git", "config", "user.name", git_user])
        await _run(["git", "config", "user.email", git_email])

        # git add
        rc, out, err = await _run(["git", "add", module_dir])
        if rc != 0:
            return {"ok": False, "error": f"git add failed: {err}"}

        # git commit
        msg = f"auto-deploy: {module_name}"
        rc, out, err = await _run(["git", "commit", "-m", msg])
        # rc 1 = nothing to commit (already committed)
        if rc not in (0, 1):
            return {"ok": False, "error": f"git commit failed: {err}"}

        # git push
        rc, out, err = await _run(["git", "push", "origin", branch])
        if rc != 0:
            return {"ok": False, "error": f"git push failed: {err}"}

        return {"ok": True, "message": f"pushed {module_name} to {branch}"}

    async def _ssh_deploy(self, module_name: str) -> Dict[str, Any]:
        host = _env_str("SELF_DEPLOY_REMOTE_SSH_HOST")
        user = _env_str("SELF_DEPLOY_REMOTE_SSH_USER")
        remote_cmd = _env_str("SELF_DEPLOY_REMOTE_COMMAND")

        if not host:
            return {"ok": False, "error": "SELF_DEPLOY_REMOTE_SSH_HOST not set"}
        if not remote_cmd:
            remote_cmd = "cd gemma_bot && git pull && docker compose restart app"

        target = f"{user}@{host}" if user else host

        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=30",
                "-o", "ServerAliveInterval=10",
                target,
                remote_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
            ok = proc.returncode == 0
            return {
                "ok": ok,
                "returncode": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace")[:2000],
                "stderr": stderr.decode("utf-8", errors="replace")[:1000] if not ok else "",
            }
        except asyncio.TimeoutError:
            return {"ok": False, "error": "SSH deploy timed out after 180s"}
        except FileNotFoundError:
            return {"ok": False, "error": "ssh command not found"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
