import pytest

from core import brain as brain_mod


@pytest.fixture(autouse=True)
def _clear_brain_env(monkeypatch):
    monkeypatch.delenv("BRAIN_TOOLS_MODE", raising=False)
    monkeypatch.delenv("BRAIN_EXTENSION_TOOLS", raising=False)


def test_filter_includes_self_programming_when_extension_on(monkeypatch):
    monkeypatch.setenv("BRAIN_TOOLS_MODE", "auto")
    monkeypatch.setenv("BRAIN_EXTENSION_TOOLS", "true")
    fake = {
        "UrlFetch.fetch_page": "x",
        "SiteRecipe.parse_with_recipe": "x",
        "DocumentCorpus.unified_search": "x",
        "SelfProgramming.generate_module": "x",
        "BooksRAG.search_book": "x",
        "SomeOther.tool": "x",
    }
    out = brain_mod._filter_tools_for_brain(fake, "просто привет")
    assert "SelfProgramming.generate_module" in out
    assert "UrlFetch.fetch_page" in out
    assert "DocumentCorpus.unified_search" in out
    assert "SomeOther.tool" not in out
    assert "BooksRAG.search_book" not in out


def test_filter_excludes_self_programming_when_extension_off(monkeypatch):
    monkeypatch.setenv("BRAIN_TOOLS_MODE", "auto")
    monkeypatch.setenv("BRAIN_EXTENSION_TOOLS", "false")
    fake = {
        "UrlFetch.fetch_page": "x",
        "SelfProgramming.generate_module": "x",
    }
    out = brain_mod._filter_tools_for_brain(fake, "привет")
    assert "SelfProgramming.generate_module" not in out


def test_filter_includes_books_rag_when_textbook_warranted(monkeypatch):
    """BRAIN_TOOLS_MODE=auto: учебник → BooksRAG при эвристике textbook_rag."""
    monkeypatch.setenv("BRAIN_TOOLS_MODE", "auto")
    fake = {
        "UrlFetch.fetch_page": "x",
        "BooksRAG.search_book": "x",
        "BooksRAG.resolve_book": "x",
        "SomeOther.tool": "x",
    }
    out = brain_mod._filter_tools_for_brain(fake, "скачай учебник по математике")
    assert "BooksRAG.search_book" in out
    assert "SomeOther.tool" not in out


def test_filter_extra_prefixes_from_env(monkeypatch):
    monkeypatch.setenv("BRAIN_TOOLS_MODE", "auto")
    monkeypatch.setenv("BRAIN_TOOLS_EXTRA_PREFIXES", "FooParser, Bar.")
    fake = {"FooParser.crawl": "x", "Bar.baz": "x", "Nope.tool": "x"}
    out = brain_mod._filter_tools_for_brain(fake, "привет")
    assert "FooParser.crawl" in out
    assert "Bar.baz" in out
    assert "Nope.tool" not in out


def test_agent_instruction_adds_self_extend_when_tool_present():
    tools = {"SelfProgramming.generate_module": "x"}
    s = brain_mod._agent_instruction_effective("auto", tools)
    assert "расширять" in s.lower() or "SelfProgramming" in s
    assert "Учебник автора плагинов" in s


def test_agent_instruction_full_mode_still_adds_extend_if_tool_present():
    tools = {"SelfProgramming.generate_module": "x", "Foo.bar": "x"}
    s = brain_mod._agent_instruction_effective("full", tools)
    assert "SelfProgramming" in s or "платформ" in s.lower()
    assert "Учебник автора плагинов" in s
