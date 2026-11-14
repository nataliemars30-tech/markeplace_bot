"""Клавиатуры: динамические Reply-меню и Inline-чекбоксы (меню v2)."""

from typing import List, Tuple
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌿 Добавить авторский"),
             KeyboardButton(text="🌹 Добавить моно")],
            [KeyboardButton(text="🎁 Композиция/Бокс"),
             KeyboardButton(text="💍 Свадебное")],
            [KeyboardButton(text="🕯 Декор и свечи"),
             KeyboardButton(text="🛍 Доп. товары")],
            [KeyboardButton(text="🔄 Копия с Flowwow")],
            [KeyboardButton(text="🔒 Скрыть букет со всех витрин")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def get_sizes_mode_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📏 1 размер")],
            [KeyboardButton(text="📐 Несколько размеров")],
            [KeyboardButton(text="◀️ Назад в главное меню")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def get_mono_flowers_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌹 Роза одноголовая"), KeyboardButton(text="🌸 Роза пионовидная")],
            [KeyboardButton(text="🌿 Роза кустовая"), KeyboardButton(text="⚪️ Хризантема")],
            [KeyboardButton(text="🌺 Гортензия"), KeyboardButton(text="🌷 Тюльпаны")],
            [KeyboardButton(text="🪻 Диантус (гвоздика)"), KeyboardButton(text="✨ Другой цветок")],
            [KeyboardButton(text="◀️ Назад в главное меню")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def get_color_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🤍 Белый"), KeyboardButton(text="🍦 Кремовый")],
            [KeyboardButton(text="🌸 Розовый"), KeyboardButton(text="🌹 Красный")],
            [KeyboardButton(text="🎨 Разноцветный"), KeyboardButton(text="🪻 Сиреневый")],
            [KeyboardButton(text="🖤 Темный"), KeyboardButton(text="🌿 Зеленый")],
            [KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


TILDA_SECTIONS: List[Tuple[str, str]] = [
    ("🌿 Стиль REN", "Стиль REN"),
    ("🌸 Нежные", "Нежные"),
    ("✨ Премиум", "Премиум"),
    ("🌹 Розы", "Розы"),
    ("🍁 Осенние", "Осенние"),
    ("❄️ Зимние", "Зимние"),
    ("🤱 День Матери", "День Матери"),
    ("🌹 Монобукеты", "Монобукеты")
]


def get_tilda_categories_inline(selected_categories: List[str]) -> InlineKeyboardMarkup:
    keyboard = []
    row = []
    for label, key in TILDA_SECTIONS:
        is_selected = key in selected_categories
        mark = "✅" if is_selected else "⬜️"
        row.append(InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"toggle_tilda:{key}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton(text="➡️ Подтвердить выбор", callback_data="confirm_tilda_selection")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
