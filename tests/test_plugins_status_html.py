from core.telegram_ui import format_plugins_status_html


def test_plugins_status_colors():
    html = format_plugins_status_html(
        [
            {"name": "a", "type": "tool", "loaded": True, "status": "healthy", "error": "", "version": "1"},
            {"name": "b", "type": "tool", "loaded": True, "status": "failed", "error": "boom", "version": None},
            {"name": "c", "type": "tool", "loaded": False, "status": "disabled", "error": ""},
        ]
    )
    assert "<pre>" in html and "</pre>" in html
    assert "Название" in html and "Статус" in html
    assert "🟢" in html and "Активен" in html and "a" in html
    assert "🟡" in html and "boom" in html
    assert "🔴" in html and "Выключен" in html and "c" in html


def test_plugins_status_empty():
    h = format_plugins_status_html([])
    assert "Нет зарегистрированных" in h
