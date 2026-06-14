# Тестирование

```bash
python -m pytest tests/ -q
python scripts/release_guard.py
python scripts/check_public_privacy.py --ci
PYTHONPATH=. python scripts/agent_security_audit.py --ci
bash scripts/pre_commit_checks.sh
```

## Пирамида

| Уровень | Команда | Назначение |
|---------|---------|------------|
| Unit / integration | `pytest tests/` | 2860+ кейсов, hot path, TurnContract |
| Smoke | `release_guard.py --smoke` | compile, plugins, privacy, lint |
| Anti-regression | `release_guard.py` | 91 файл «уже ломалось» |
| Privacy pre-commit | `pre_commit_checks.sh` | PII в git **до коммита** |
| Corpus + LLM | `build_test_corpus.py` + `agent_test_runner.py` | Реальные маршруты, validators |
| Mutation L2 | `mutation_guard_l2.py` (weekly CI) | mutmut на pure guards |
| Life-sim | `tests/test_turn_hot_path_integration.py` | Случайные эпизоды, инварианты defer/finalize |
| Structural replay | `replay_turn_thread.py --regression` | 20 кейсов без LLM |
| CodeQL | GitHub Security tab | Static analysis (отдельно от pytest) |

Зелёный pytest ≠ проверка в TG — после смены поведения smoke в Telegram.

## Life-sim (случайные эпизоды)

```bash
python -m pytest tests/test_turn_hot_path_integration.py -q
TURN_LIFE_SIM_EPISODES=12 python -m pytest tests/test_turn_hot_path_integration.py -q
TURN_LIFE_SIM_CHAOS=1 python -m pytest tests/test_turn_hot_path_integration.py::TurnHotPathIntegrationTests::test_life_sim_chaos_episode -q
TURN_LIFE_SIM_SEED=424242 python -m pytest tests/test_turn_hot_path_integration.py -q
```

## Corpus (продвинутый прогон)

```bash
python scripts/build_test_corpus.py --target 500
python scripts/agent_test_runner.py --tier smoke --limit 50
python scripts/full_system_integration_probe.py
```

## Безопасность

См. [docs/security/CODE_SCANNING_RU.md](../security/CODE_SCANNING_RU.md)

См. `tests/README.md`
