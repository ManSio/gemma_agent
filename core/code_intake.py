from __future__ import annotations

import ast
import json
import os
from typing import Any, Dict, List

from core.error_analysis import record_error_event


SUPPORTED_CODE_EXT = {".py", ".go", ".js", ".ts", ".html", ".css", ".json", ".yaml", ".yml"}


class CodeIntakeLayer:
    def __init__(self) -> None:
        self.enabled = os.getenv("CODE_INTAKE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.max_file_kb = int(os.getenv("CODE_INTAKE_MAX_FILE_KB", "512"))

    def _read_text(self, path: str) -> str:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = f.read(int(self.max_file_kb * 1024 + 1))
        return data[: int(self.max_file_kb * 1024)]

    def analyze_file(self, path: str) -> Dict[str, Any]:
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext not in SUPPORTED_CODE_EXT:
                return {"ok": False, "error": "unsupported_extension", "ext": ext}
            text = self._read_text(path)
            result: Dict[str, Any] = {"ok": True, "ext": ext, "path": path, "size": len(text)}
            if ext == ".py":
                try:
                    tree = ast.parse(text)
                    funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
                    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
                    result["functions"] = funcs[:200]
                    result["classes"] = classes[:200]
                except Exception as e:
                    result["syntax_error"] = str(e)
            elif ext == ".json":
                try:
                    obj = json.loads(text)
                    result["json_valid"] = True
                    result["keys"] = list(obj.keys())[:100] if isinstance(obj, dict) else []
                except Exception as e:
                    result["json_valid"] = False
                    result["json_error"] = str(e)
            result["preview"] = text[:3000]
            return result
        except Exception as e:
            record_error_event("code_intake", "analyze_file failed", exc=e, extra={"path": path})
            return {"ok": False, "error": str(e)}

    def analyze_project(self, root: str) -> Dict[str, Any]:
        report: Dict[str, Any] = {"ok": True, "root": root, "files": [], "deps": []}
        try:
            for dirpath, _, filenames in os.walk(root):
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    ext = os.path.splitext(full)[1].lower()
                    if ext in SUPPORTED_CODE_EXT:
                        report["files"].append(full)
                    if fn in {"requirements.txt", "package.json", "pyproject.toml", "go.mod"}:
                        report["deps"].append(full)
                if len(report["files"]) > 1000:
                    break
            report["file_count"] = len(report["files"])
            return report
        except Exception as e:
            record_error_event("code_intake", "analyze_project failed", exc=e, extra={"root": root})
            return {"ok": False, "error": str(e)}

    def lint_hint(self, path: str) -> Dict[str, Any]:
        ext = os.path.splitext(path)[1].lower()
        return {
            "ok": True,
            "path": path,
            "lint_recommendation": {
                ".py": "ruff/flake8",
                ".js": "eslint",
                ".ts": "eslint+tsc",
                ".go": "golangci-lint",
            }.get(ext, "basic static checks"),
        }

    def _priority(self, issue: str) -> str:
        low = (issue or "").lower()
        if any(k in low for k in ("syntax", "crash", "exception", "security")):
            return "high"
        if any(k in low for k in ("lint", "style", "format")):
            return "low"
        if any(k in low for k in ("refactor", "cleanup", "readability")):
            return "medium"
        return "medium"

    def build_unified_diff_template(
        self,
        path: str,
        *,
        old_snippet: str = "# old code",
        new_snippet: str = "# new code",
        reason: str = "targeted improvement",
    ) -> str:
        return (
            f"--- a/{path}\n"
            f"+++ b/{path}\n"
            f"@@\n"
            f"-{old_snippet}\n"
            f"+{new_snippet}\n"
            f"# reason: {reason}\n"
        )

    def propose_patch(self, path: str, issue: str) -> Dict[str, Any]:
        prio = self._priority(issue)
        patch = self.build_unified_diff_template(
            path,
            old_snippet="# old code",
            new_snippet="# new code",
            reason=issue,
        )
        return {
            "ok": True,
            "path": path,
            "issue": issue,
            "priority": prio,
            "proposal": "Create a minimal targeted patch with tests and keep public contract intact.",
            "unified_diff_template": patch,
        }

    def _risk_for_issue(self, issue: str) -> str:
        low = (issue or "").lower()
        if any(k in low for k in ("security", "auth", "permission")):
            return "high"
        if any(k in low for k in ("syntax", "exception", "crash")):
            return "medium"
        if any(k in low for k in ("lint", "format", "style", "readability")):
            return "low"
        return "medium"

    def _test_plan_for_issue(self, issue: str, path: str) -> List[str]:
        ext = os.path.splitext(path)[1].lower()
        generic = [
            "Run focused smoke test for modified behavior.",
            "Run repository linters on touched files.",
            "Verify no regressions in adjacent functionality.",
        ]
        if ext == ".py":
            generic.insert(0, "Run Python tests covering affected module.")
        elif ext in {".js", ".ts"}:
            generic.insert(0, "Run JS/TS unit tests and type checks.")
        elif ext == ".go":
            generic.insert(0, "Run go test for affected package.")
        if "lint" in (issue or "").lower():
            generic.insert(0, "Run linter and confirm warning/error count decreases.")
        return generic[:5]

    def build_patch_pack(self, path: str, issue: str) -> Dict[str, Any]:
        patch_plan = self.propose_patch(path, issue)
        priority = patch_plan.get("priority", "medium")
        risk = self._risk_for_issue(issue)
        test_plan = self._test_plan_for_issue(issue, path)
        return {
            "ok": True,
            "path": path,
            "priority": priority,
            "risk": risk,
            "patch": patch_plan.get("unified_diff_template", ""),
            "rationale": patch_plan.get("proposal", ""),
            "test_plan": test_plan,
        }

    def build_patch_pack_multi(self, path: str, issues: List[str]) -> Dict[str, Any]:
        packs = [self.build_patch_pack(path, it) for it in issues if (it or "").strip()]
        order = {"high": 0, "medium": 1, "low": 2}
        packs.sort(key=lambda x: order.get(str(x.get("priority", "medium")), 1))
        return {
            "ok": True,
            "path": path,
            "count": len(packs),
            "items": packs,
        }

    def engineer_cycle(self, context: Dict[str, Any], user_text: str) -> Dict[str, Any]:
        code_info = context.get("code_intake") if isinstance(context, dict) else {}
        if not isinstance(code_info, dict):
            code_info = {}
        raw = user_text.strip() or "refactor and lint cleanup"
        # Split issues by separators for a small reviewable patch pack.
        issues = [x.strip() for x in raw.replace("\n", ";").split(";") if x.strip()]
        if not issues:
            issues = ["refactor and lint cleanup"]
        issue = issues[0]
        path = str(code_info.get("path") or "unknown_file")
        lint = self.lint_hint(path) if path != "unknown_file" else {"ok": True, "lint_recommendation": "basic static checks"}
        patch = self.propose_patch(path, issue)
        patch_pack = self.build_patch_pack_multi(path, issues)
        return {
            "ok": True,
            "analysis": code_info,
            "lint": lint,
            "patch_plan": patch,
            "patch_pack": patch_pack,
            "next_steps": [
                "reproduce issue",
                "write minimal patch",
                "run linter/tests",
                "prepare changelog note",
            ],
        }
