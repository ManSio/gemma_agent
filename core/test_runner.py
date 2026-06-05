"""
Test Runner for modules and libraries — запускает pytest для модулей.
"""
import os
import sys
import subprocess
import logging
from typing import Any, Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class TestRunner:
    """Runner for executing tests on modules and libraries через pytest."""

    TEST_RESULTS: Dict[str, Dict[str, Any]] = {}

    async def run_module_tests(self, module_path: Path) -> bool:
        """Run tests for a specific module using pytest."""
        try:
            tests_file = module_path / "tests.py"
            if not tests_file.exists():
                logger.info("[test_runner] No tests for %s, skipped (not a failure)", module_path.name)
                TestRunner.TEST_RESULTS[module_path.name] = {
                    "passed": True,
                    "skipped": True,
                    "output": "",
                }
                return True

            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(tests_file), "-q", "--tb=short", "--no-header"],
                capture_output=True, text=True, timeout=120,
            )

            TestRunner.TEST_RESULTS[module_path.name] = {
                "passed": result.returncode == 0,
                "output": result.stdout + result.stderr,
            }

            if result.returncode == 0:
                logger.info("[test_runner] Module %s tests PASSED", module_path.name)
                return True

            logger.warning(
                "[test_runner] Module %s tests FAILED:\n%s",
                module_path.name, (result.stdout + result.stderr)[:500],
            )
            return False

        except subprocess.TimeoutExpired:
            logger.warning("[test_runner] Module %s tests TIMEOUT", module_path.name)
            return False
        except Exception as e:
            logger.error("[test_runner] Error testing module %s: %s", module_path.name, e)
            return False

    async def run_library_tests(self, library_path: Path) -> bool:
        """Run tests for a library module using pytest."""
        try:
            tests_file = library_path / "tests.py"
            if not tests_file.exists():
                logger.info(
                    "[test_runner] No tests for library %s, skipped (not a failure)",
                    library_path.name,
                )
                TestRunner.TEST_RESULTS[library_path.name] = {
                    "passed": True,
                    "skipped": True,
                    "output": "",
                }
                return True

            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(tests_file), "-q", "--tb=short", "--no-header"],
                capture_output=True, text=True, timeout=120,
            )

            TestRunner.TEST_RESULTS[library_path.name] = {
                "passed": result.returncode == 0,
                "output": result.stdout + result.stderr,
            }

            if result.returncode == 0:
                logger.info("[test_runner] Library %s tests PASSED", library_path.name)
                return True

            logger.warning(
                "[test_runner] Library %s tests FAILED:\n%s",
                library_path.name, (result.stdout + result.stderr)[:500],
            )
            return False

        except subprocess.TimeoutExpired:
            logger.warning("[test_runner] Library %s tests TIMEOUT", library_path.name)
            return False
        except Exception as e:
            logger.error("[test_runner] Error testing library %s: %s", library_path.name, e)
            return False
