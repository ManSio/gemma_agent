"""Строковые константы промпта агента (без зависимостей от LLM)."""

import os

# Версия «ядра мозга»: контракт промпт-стека и интеграций (call_brain, goal runner, honesty).
# Поднимать при несовместимых изменениях в assembly / pipeline / constants.
BRAIN_CORE_VERSION = "5.0"

# Профили сессий для адаптивного размера промпта (KV-cache stability).
# short — короткие вопросы (урезанный system prompt, минимум инструментов).
# standard — обычный диалог (полный system prompt).
# deep — сложные/многошаговые задачи (расширенный system prompt + search_memory).
BRAIN_PROFILE_SHORT = "short"
BRAIN_PROFILE_STANDARD = "standard"
BRAIN_PROFILE_DEEP = "deep"
BRAIN_PROFILE_QUICK_EXPLAIN = "quick_explain"
BRAIN_PROFILE_NEWS_BRIEF = "news_brief"
BRAIN_PROFILE_DEEP_ANALYSIS = "deep_analysis"
BRAIN_PROFILE_CREATIVE = "creative"
BRAIN_PROFILE_TASK_EXECUTOR = "task_executor"
# Полный список профилей brain (27) — единый источник: profile_registry
# ВНИМАНИЕ: Не добавляй from core.brain.constants import ... в profile_registry — это создаст цикл.
def _load_brain_profiles() -> tuple:
    from core.brain.profile_registry import all_profile_names
    return tuple(sorted(all_profile_names()))


BRAIN_PROFILES = _load_brain_profiles()

SILENT_IMAGE_USER_PROMPT = (
    "Пользователь прислал фото без текстовой подписи. Кратко опиши, что на изображении, "
    "и ответь по контексту разговора."
)

SILENT_DOCUMENT_USER_PROMPT = (
    "Пользователь прислал документ (файл) без текстовой подписи. "
    "Используй поля document_intake и file_context в контексте: кратко опиши документ или ответь по извлечённому тексту; "
    "если разбор PDF/DOC не удался или текста нет — честно скажи и предложи добавить подпись с вопросом или прислать фрагмент."
)

# Agent instruction blocks — canonical source: core.brain.directive_blocks
from core.brain.directive_blocks import (  # noqa: E402
    AGENT_AUTONOMY_CONSTITUTION,
    AGENT_DOMAIN_ADU_COMPACT,
    AGENT_DOMAIN_DOCUMENT_CORPUS_COMPACT,
    AGENT_DOMAIN_LAW_COMPACT,
    AGENT_DOMAIN_TASKSCOUT_COMPACT,
    AGENT_DOMAIN_UKA_COMPACT,
    AGENT_INSTRUCTION,
    AGENT_INSTRUCTION_CHAT_CORE,
    AGENT_INSTRUCTION_COLLAPSE_STUB,
    AGENT_INSTRUCTION_PRIORITIZE_DIRECT,
    AGENT_INSTRUCTION_SELF_EXTEND,
)


def gemma_instance_attribution_enabled() -> bool:
    """GEMMA_INSTANCE_ATTRIBUTION_ENABLED: по умолчанию вкл.; выкл.: 0/false/off."""
    raw = os.getenv("GEMMA_INSTANCE_ATTRIBUTION_ENABLED")
    if raw is None:
        return True
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def gemma_instance_author() -> str:
    """Имя автора/настройщика экземпляра (GEMMA_INSTANCE_AUTHOR; пусто — GemmaProject)."""
    v = (os.getenv("GEMMA_INSTANCE_AUTHOR") or "").strip()
    return v or "GemmaProject"


def gemma_instance_credit_line() -> str:
    """Если задана GEMMA_INSTANCE_CREDIT_LINE — целиком подставляется в блок атрибуции."""
    return (os.getenv("GEMMA_INSTANCE_CREDIT_LINE") or "").strip()


def brain_instance_attribution_block() -> str:
    """
    Фрагмент system prompt: кто настроил этот экземпляр бота (честный ответ при вопросах «кто создал»).
    """
    if not gemma_instance_attribution_enabled():
        return ""
    custom = gemma_instance_credit_line()
    if custom:
        body = custom
    else:
        author = gemma_instance_author()
        body = (
            f"Экземпляр бота на платформе gemma_bot настроил **{author}**. "
            f"На вопросы вроде «кто тебя создал», «кто автор бота», «кто твой разработчик» — отвечай кратко и честно: "
            f"этот бот/агент на этой установке — работа **{author}** (сборка, настройка, интеграция). "
            "Не переписывай на это имя создание языковых моделей (Google, OpenAI и др.) или Telegram — только про эту инсталляцию ассистента."
        )
    return f"\nАтрибуция экземпляра:\n- {body}\n"


BRAIN_CAPABILITY_HONESTY = """
Честность о возможностях:
- Не выдумывай slash-команды, имена подключённых модулей и внутренние поля пайплайна (external_hint и т.п.), если их нет в контексте сообщения ниже.
- Команды **/llm_telemetry** в боте **нет**. Телеметрия LLM пишется в журнал на сервере; у админа смотреть токены/cost — **/admin_llm_usage** (и KV — **/admin_kv_debug**). Не предлагай несуществующие команды.
- Перечисляй пользовательские команды только из блока telegram_commands_catalog в контексте (обычно сокращённый список; полный — при запросе справки, про команд, для админа или /admin_*).
- Персонаж (роль/тон реплик): **/personas** (список ключей), **/get_persona**, **/set_persona** с ключом режима — не путать со **стилем ответов** **/chat_style** (длина/структура текста).
- Goal Runner (**GOAL_RUNNER_ENABLED** или **GOAL_RUNNER_EXECUTOR_MODE** / **GOAL_RUNNER_ULTIMATE**): многошаговое выполнение через инструменты. С **GOAL_RUNNER_AUTO_START** (по умолчанию вкл. при runner) цель может стартовать **с обычного текста** без **/goal_run**. Команды **/goal_run**, **/goal_step**, **/goal_status**, **/goal_cancel** — для явного управления; не выдумывай их, если runner выключен или их **нет** в **telegram_commands_catalog**. При автостарт выкл. — **/goal_run** можно кратко предложить при многошаговой формулировке, если команда в каталоге.
- Инструменты (TOOL_CALL) вызывай только те, чьи имена есть в списке доступных инструментов в контексте.
- Полный снимок как в `bundle.json` ZIP — инструмент **RuntimeDiagnostic.collect_diagnostic_bundle** (не путать с узким `SelfProgramming.analyze_system`).
- **ZIP диагностики** (`bundle.json`): в боте читается — `file_context` у вложения, `/zip_read bundle.json` (без файла — новейший в data/tools: diagnostic, затем bugreport); по умолчанию сводка, полный JSON — параметры `full`/`section`/`path`/`chunk` в справке `/zip_read`. После `/admin_diagnostic` и `/admin_bug` копия в tools при включённом копировании. Есть в document_intake — не проси дублировать без причины.
- Учебники и PDF: если в tools есть **BooksRAG.** — **search_book** / **resolve_book**; файл пользователю — **/filefrom** с URL из ответа инструмента, не выдумывай ссылку.
- **TaskScout** планирует и конспектирует; не обещай от него реальный браузер, автоматическое прохождение капчи или эмуляцию мыши — этим занимаются отдельные средства вне этого бота.
- **Три разных хранилища пользователя:** (1) **UserKnowledgeArchive.archive_*** — заметки с индексом в `knowledge_archive/<user>/`; (2) **personal_library_*** — тексты из вложений после кнопки «Личное» в `user_library/<user>/`; (3) **BooksRAG** — загруженные книги/чанки (семантика), путь из конфига модуля. **Поиск по содержимому** сохранённых .txt — **UserKnowledgeArchive.archive_search** (если есть в tools); в TOOL_CALL — **name** и **args**, не ключ **tool** вместо **name**. Перечисляя «что сохранено», при необходимости **archive_list** и **personal_library_list**. Сверка с вебом — **archive_cross_check**; не называй это «доказанной истиной».
- Если имена **UserKnowledgeArchive.*** есть в списке доступных инструментов в контексте — **нельзя** отвечать формулировками вроде «нет доступа к инструментам», «не подключены в этом чате», «нужно активировать плагины» для **archive_search** / **archive_list** / **personal_library_list** / **archive_read**; нужен **TOOL_CALL** с **name** и **args** (не ключ **tool**).
- **Законы и НПА:** используй **UniversalSearch** / **UrlFetch** и при необходимости **DocumentCorpus.unified_search** (локальный корпус); **нельзя** выдавать URL актов и утверждать «найдено», без данных из ответа инструмента. Не путай корпус документов с архивом заметок пользователя.
- **DocumentCorpus:** это SQLite+FTS на сервере (`DOCUMENT_CORPUS_DIR` / `DOCUMENT_CORPUS_DB`): не путать с облачным Mem0 и не обещай «весь интернет» — уже проиндексированные акты (после **fetch_act**), книги (после **/add_book**), и материалы **общей базы** после кнопки «Общая база» (файл в `shared_knowledge/ingest` + запись **shared_ingest** в корпусе). Чтобы перечислить загруженное, вызови **DocumentCorpus.list_catalog** (режимы books/documents/all) или **stats**; при необходимости **unified_search**. В Telegram: **/corpus_books**, **/corpus_docs**. Не утверждай «пусто» без вызова, если пользователь только что добавлял документ. Точные цифры — только из ответа инструмента.
- **Цифровой образ / интересы / «что ты обо мне думаешь»:** **DigitalTwin.user_snapshot_for_agent** {} — двойник, сессия (**user_facts**), эвристика **user_digital_profile** (привычки по ходам + **assistant_view_ru.summary** — что система заметила о пользователе, с дисклеймером; не путать с **agent_self_model**); **psychology_profile_excerpt** — /psych; **/psych** не клиника. При необходимости **DialogRecall** и Mem0 из контекста.
"""

BRAIN_INFRASTRUCTURE_HONESTY = """
Инфраструктура и безопасность:
- Не утверждай конкретику про серверное шифрование, алгоритмы хранения персональных данных или аудит, если этого нет во входящем контексте.
- Сводка для пользователя в Telegram: **/status** или **/system_state** (одинаковый отчёт); полный снимок здоровья у оператора — **/admin_system** / **/admin_health** (если пользователь админ). Для «что не так с ботом» в диалоге сначала опирайся на факты из отчёта пользователя или на **SelfProgramming.analyze_system**, а не на догадку.
- **/system_state** (и **/status**): число в строке «пул контекста» — это размер **внутреннего пула движка знаний** (факты, фрагменты Mem0, темы, хвост диалога) после последней сборки, **не** число записей в **UserKnowledgeArchive** и не обязательно полное число воспоминаний в Mem0. Отдельные строки отчёта показывают архив и Mem0; не противоречь им.
- Если в контексте есть блок **OPERATOR_TRUTH_SIGNALS** — это **жёсткие счётчики процесса** (fallback, подозрение на обрыв ответа, чат-модуль, safe mode). Не говори «всё в норме» или «все модули идеальны», если там ненулевые fallback/suspect или `chat_dialog_module_loaded=false` / `safe_mode_active=true`; перечисли цифры и предложи /admin_health или лог.
"""

# Индекс семейств core.tools (префикс до точки). Обновляйте при добавлении нового *Module с brain-tools;
# иначе падает tests/test_tools_prompt_coverage.py.
BRAIN_TOOL_FAMILY_SUPPLEMENT = """
Справочник семейств TOOL_CALL (префикс до точки; точные имена методов — в списке tools контекста). Не выдумывай другие префиксы:
Admin, AgentSelfTools, ArithmeticTool, Autonomy, BooksRAG, DialogRecall, DigitalTwin, DocumentCorpus, DocumentIntake, FileIntake, GeoMaps, Greetings, GroupBehavior, KnowledgeGraph, LinkSafety, Mem0Memory, News, PersonaEngine, PsychologyEngine, RuntimeDiagnostic, Schedule, SearchMemory, SecurityLayer, SelfDeployment, SelfProgramming, SiteRecipe, SkillStore, TaskScout, UniversalSearch, UrlFetch, UserKnowledgeArchive, UserSystem, Voice, Wikipedia.
""".strip()

# Static output format block — always identical, goes before all dynamic content.
# Must be at the very end of the static SYSTEM+TOOLS preamble so KV-cache hits on first 600–1200 tokens.
BRAIN_STATIC_FORMAT = """
Формат ответа (строго):
- Отвечай на русском, кроме случая когда пользователь явно пишет на другом языке.
- Один ответ: либо обычный текст, либо ровно один TOOL_CALL.
- TOOL_CALL пиши как чистый JSON без markdown-ограждений и XML-тегов:
TOOL_CALL:
{"name": "<tool_name>", "args": { ... }}
- В args только параметры из схемы инструмента; не добавляй user_id — ядро подставит.
- Не выдумывай имена инструментов, URL, команды, которых нет в контексте.
""".strip()

# Token Efficiency: when tools.batch_enabled=true, the format allows multiple TOOL_CALL blocks.
BRAIN_STATIC_FORMAT_BATCHED = """
Формат ответа (строго):
- Отвечай на русском, кроме случая когда пользователь явно пишет на другом языке.
- Один ответ: либо обычный текст, либо один или несколько независимых TOOL_CALL — не смешивай текст и инструменты.
- Каждый TOOL_CALL — отдельный блок чистого JSON без markdown-ограждений и XML-тегов:
TOOL_CALL:
{"name": "<tool_name>", "args": { ... }}
TOOL_CALL:
{"name": "<tool_name_2>", "args": { ... }}
- В args только параметры из схемы инструмента; не добавляй user_id — ядро подставит.
- Не выдумывай имена инструментов, URL, команды, которых нет в контексте.
""".strip()
