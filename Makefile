PY ?= python

.PHONY: smoke smoke-strict tests anti-regression release-guard release-guard-full lint run-bot run-api

smoke:
	$(PY) -m py_compile main.py api.py

smoke-strict:
	$(PY) scripts/release_guard.py --smoke

anti-regression:
	$(PY) -m pytest -q tests/test_command_catalog.py tests/test_pending_flow.py \
		tests/test_answer_quality.py tests/test_plugin_contract.py tests/test_plugin_admin_ops.py \
		tests/test_slash_exclusive.py tests/test_inline_slash_dispatch.py \
		tests/test_local_ops_modules.py tests/test_orchestrator_plugin_command_routing.py \
		tests/test_user_image_pending.py tests/test_admin_bug_report.py \
		tests/test_agent_test_runner_dialog_turns.py \
		tests/test_autopilot_mode.py tests/test_llm_task_outline.py

release-guard:
	$(PY) scripts/release_guard.py

release-guard-full:
	$(PY) scripts/release_guard.py --full

tests:
	$(PY) -m pytest -q tests/

lint:
	@echo "Use IDE lint integration or project linter command."

run-bot:
	$(PY) main.py

run-api:
	$(PY) api.py
