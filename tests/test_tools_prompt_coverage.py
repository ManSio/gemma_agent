"""CI: каждое семейство из core.tools должно быть упомянуто в промпт-бандле (честность агента)."""
from __future__ import annotations

from core import tools
from core.brain.prompt_coverage import brain_prompt_text_bundle


def test_every_tool_prefix_appears_in_prompt_bundle():
    tools._ensure_tools_scan()
    blob = brain_prompt_text_bundle()
    prefixes = sorted({str(k).split(".", 1)[0] for k in tools.TOOLS.keys()})
    missing = [p for p in prefixes if p not in blob]
    assert not missing, (
        "Добавьте префикс(ы) в core/brain/constants.py (блоки AGENT_* / BRAIN_*) "
        f"или в BRAIN_TOOL_FAMILY_SUPPLEMENT: {missing}\n"
        f"Известные префиксы сейчас: {prefixes}"
    )


def test_tool_family_supplement_comma_line_matches_tools():
    """Первая строка со списком через запятую = полный набор префиксов (без расхождений)."""
    import re

    from core.brain.constants import BRAIN_TOOL_FAMILY_SUPPLEMENT

    tools._ensure_tools_scan()
    prefs = {str(k).split(".", 1)[0] for k in tools.TOOLS.keys()}
    m = re.search(r"префиксы:\s*\n([^\n]+)", BRAIN_TOOL_FAMILY_SUPPLEMENT, flags=re.IGNORECASE)
    assert m, "BRAIN_TOOL_FAMILY_SUPPLEMENT: ожидается строка «...префиксы:» и следующая строка со списком через запятую"
    listed = {x.strip() for x in m.group(1).rstrip(".").split(",") if x.strip()}
    assert prefs == listed, (
        "Обновите список через запятую в BRAIN_TOOL_FAMILY_SUPPLEMENT "
        f"(constants.py): лишние {listed - prefs}, пропущены {prefs - listed}"
    )
