"""Общая навигация inline-клавиатур для /help и /admin (пагинация, footer)."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# (label, admin callback key без префикса admin:)
ADMIN_TILES: Tuple[Tuple[str, str], ...] = (
    ("📊 Обзор", "dashboard"),
    ("💚 Здоровье", "health"),
    ("🛡️ Устойчив.", "resilience"),
    ("🫀 Пульс", "pulse"),
    ("🩻 Рентген", "xray"),
    ("📉 LLM", "llm_usage"),
    ("📈 Счётчики", "stats"),
    ("⭐ Репутация", "reputation"),
    ("🧠 Обучение", "learning"),
    ("🚦 Антифлуд", "antiflood"),
    ("📜 Журнал", "logs"),
    ("📋 Данные", "governance"),
    ("💾 Бэкапы", "backups"),
    ("📜 Паспорт", "passport"),
    ("🤖 Автономия", "auto"),
    ("🎯 Навыки", "skills"),
    ("👥 Пользов.", "users"),
    ("📖 Команды", "commands"),
    ("🎛 Оператор", "operator"),
    ("⚙️ Настройки", "settings"),
)

ADMIN_TILES_PER_PAGE = 6  # 3 ряда × 2 кнопки


def admin_menu_page_count() -> int:
    n = len(ADMIN_TILES)
    return max(1, (n + ADMIN_TILES_PER_PAGE - 1) // ADMIN_TILES_PER_PAGE)


def _pair_row(items: Sequence[Tuple[str, str]]) -> List[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text=label, callback_data=f"admin:{key}")
        for label, key in items
    ]


def build_admin_menu_keyboard(page: int = 1) -> InlineKeyboardMarkup:
    """Пагинированное меню панели админа."""
    total = admin_menu_page_count()
    page = max(1, min(page, total))
    start = (page - 1) * ADMIN_TILES_PER_PAGE
    chunk = list(ADMIN_TILES[start : start + ADMIN_TILES_PER_PAGE])
    rows: List[List[InlineKeyboardButton]] = []
    for i in range(0, len(chunk), 2):
        rows.append(_pair_row(chunk[i : i + 2]))
    nav: List[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"admin:menu_{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"· {page}/{total} ·", callback_data=f"admin:menu_{page}"))
    if page < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"admin:menu_{page + 1}"))
    rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(text="📄 Рецепт сайта", callback_data="sr:b"),
            InlineKeyboardButton(text="📎 Сиды", callback_data="admin:seed_menu"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_detail_footer_rows(*, menu_page: int = 1) -> List[List[InlineKeyboardButton]]:
    """Компактный footer на экранах разделов (не полное меню)."""
    return [
        [
            InlineKeyboardButton(text="◀️ Панель", callback_data="admin:dashboard"),
            InlineKeyboardButton(text="📖 Команды", callback_data="admin:commands"),
            InlineKeyboardButton(text="⚙️ Меню", callback_data="admin:menu_1"),
        ],
    ]


def merge_keyboards(
    primary: Optional[InlineKeyboardMarkup],
    extra_rows: List[List[InlineKeyboardButton]],
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if primary and primary.inline_keyboard:
        rows.extend(list(primary.inline_keyboard))
    rows.extend(extra_rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def help_hub_nav_rows(*, active: str = "main") -> List[List[InlineKeyboardButton]]:
    """Верхняя навигация справки (всегда одна строка)."""

    def _prefix(page: str) -> str:
        if page == "modules_1":
            return "▸ " if (active or "").startswith("modules") else ""
        return "▸ " if active == page else ""

    return [
        [
            InlineKeyboardButton(text=f"{_prefix('main')}Старт", callback_data="help:main"),
            InlineKeyboardButton(text=f"{_prefix('user')}Вы", callback_data="help:user"),
            InlineKeyboardButton(text=f"{_prefix('user_more')}Ещё", callback_data="help:user_more"),
            InlineKeyboardButton(text=f"{_prefix('modules_1')}Плагины", callback_data="help:modules_1"),
        ],
        [
            InlineKeyboardButton(text=f"{_prefix('images')}🖼 Картинки", callback_data="help:images"),
        ],
    ]


def help_admin_obs_nav_rows(*, page: int = 1) -> List[List[InlineKeyboardButton]]:
    """Пагинация длинного раздела «Метрики» в /help."""
    rows: List[List[InlineKeyboardButton]] = []
    nav: List[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data="help:admin_obs"))
    nav.append(InlineKeyboardButton(text=f"Метрики {page}/2", callback_data="help:admin_obs"))
    if page < 2:
        nav.append(InlineKeyboardButton(text="▶️", callback_data="help:admin_obs_2"))
    if nav:
        rows.append(nav)
    return rows
