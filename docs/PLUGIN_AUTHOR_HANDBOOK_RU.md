# Учебник автора плагинов

Public-build guide for `SelfProgramming.generate_module` and manual edits under `modules/`.

## 1. Когда генерировать

- Пользователь явно просит **новую** возможность / slash-команду / модуль.
- Не генерировать «на всякий случай» и не править прод без запроса.

## 2. Структура каталога

```
modules/<module_name>/
  module.json
  module.py
  README.md          # optional
```

`module_name` — латиница, `snake_case`, совпадает с именем папки и полем `name` в JSON.

## 3. Манифест `module.json`

Обязательные поля (см. живые примеры в `modules/`):

| Поле | Описание |
|------|----------|
| `name` | Имя плагина |
| `type` | Обычно `module`; не выдумывайте обязательный `tool` |
| `entrypoint` | `modules.<name>.module:YourClass` |
| `commands` | Slash-команды для прямого вызова |
| `capabilities` | Intent-строки для маршрутизации обычного текста |
| `pip_requirements` | Список PyPI; после изменений — `merge_plugin_requirements.py` |

Валидация: `core/plugin_contract.py`, smoke — `scripts/release_guard.py`.

## 4. Класс модуля

```python
from core.models import Output

class YourClass:
    async def execute(self, args: dict):
        input_data = args.get("input") or {}
        context = args.get("context") or {}
        text = str(input_data.get("payload", ""))
        return Output(content="...", module=self.__class__.__name__)
```

Возврат: один `Output` или `list[Output]`.

## 5. Как ядро выбирает модуль

1. **Slash** — первый символ `/` → поиск по `commands` во всех манифестах.
2. **Текст** — intent из orchestrator → первый загруженный модуль с matching `capabilities`.
3. **general** — предпочтение диалогу (`chat-orchestrator`), не «случайному» модулю.

Новый capability (например `stress`) **не заработает сам** без правок `core/orchestrator.py` / `core/intent_heuristics.py`. Если нужен только slash — достаточно `commands`.

## 6. SelfProgramming API

| Tool | When |
|------|------|
| `generate_module` | Новый модуль: `module_name`, `description`, опционально `commands`, `pip_requirements`, … |
| `self_repair_module` | Починка существующего |
| `analyze_system` | Аудит реестра (не архив) |

После `generate_module`: если `hot_install.success=true` — реестр подхватил без рестарта.

## 7. Тесты и качество

- Локальный `modules/<name>/tests.py` ядром не прогоняется.
- Добавляйте тесты в корневой `tests/` и запускайте `pytest tests/`.
- Перед PR: `python scripts/release_guard.py`.

## 8. Безопасность

- Не предлагайте небезопасный код (eval, shell без нужды, exfiltration).
- Не включайте секреты в сгенерированные файлы.
- Обсуждение кода плагина ≠ запрос к `/calc` или ArithmeticTool.

Краткая версия для промпта: [PLUGIN_AI_CRITICAL_BRIEF.md](PLUGIN_AI_CRITICAL_BRIEF.md).
