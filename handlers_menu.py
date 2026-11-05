"""Хэндлеры динамического кнопочного меню (навигация, v2)."""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from states import ProductCreationFSM, HideFSM
from keyboards import (
    get_main_reply_keyboard,
    get_sizes_mode_keyboard,
    get_mono_flowers_keyboard,
    get_color_keyboard,
    get_tilda_categories_inline
)

router = Router()

# Отображение кнопок -> тип товара в конвейере (root_category)
CATEGORY_MAP = {
    "🌿 Добавить авторский": ("author", "Букеты"),
    "🎁 Композиция/Бокс": ("box", "Композиции"),
    "💍 Свадебное": ("wedding", "Свадебные"),
    "🕯 Декор и свечи": ("decor", "Декор"),
    "🛍 Доп. товары": ("extra", "Доп. товары"),
}


@router.message(F.text.in_(["◀️ Назад в главное меню", "◀️ Назад"]))
async def handle_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=get_main_reply_keyboard())


@router.message(F.text == "🔒 Скрыть букет со всех витрин")
async def start_hide(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(HideFSM.waiting_article)
    await message.answer(
        "Введите артикул букета для скрытия (например, REN-042 или REN-042-M):",
        reply_markup=get_main_reply_keyboard()
    )


# --- ВЕТКА: АВТОРСКИЙ / КОМПОЗИЦИЯ / СВАДЕБНОЕ / ДЕКОР / ДОП. ТОВАРЫ ---
@router.message(F.text.in_(list(CATEGORY_MAP.keys())))
async def start_categorized(message: Message, state: FSMContext):
    await state.clear()
    ptype, root_cat = CATEGORY_MAP[message.text]
    await state.update_data(product_type=ptype, root_category=root_cat)
    if ptype == "author":
        await state.set_state(ProductCreationFSM.sizes_mode)
        await message.answer("Выберите формат размеров:", reply_markup=get_sizes_mode_keyboard())
    else:
        # Для остальных категорий размеры не спрашиваем — сразу цвет
        await state.update_data(sizes_mode="single")
        await state.set_state(ProductCreationFSM.color)
        await message.answer("Выберите цветовую гамму:", reply_markup=get_color_keyboard())


@router.message(ProductCreationFSM.sizes_mode, F.text.in_(["📏 1 размер", "📐 Несколько размеров"]))
async def select_sizes_mode(message: Message, state: FSMContext):
    mode = "single" if "1 размер" in message.text else "multi"
    await state.update_data(sizes_mode=mode)
    await state.set_state(ProductCreationFSM.color)
    await message.answer("Выберите цветовую гамму букета:", reply_markup=get_color_keyboard())


# --- ВЕТКА: МОНОБУКЕТ ---
@router.message(F.text == "🌹 Добавить моно")
async def start_mono_bouquet(message: Message, state: FSMContext):
    await state.clear()
    await state.update_data(product_type="mono", root_category="Букеты", sizes_mode="multi")
    await state.set_state(ProductCreationFSM.flower_type)
    await message.answer("Выберите цветок для монобукета:", reply_markup=get_mono_flowers_keyboard())


@router.message(ProductCreationFSM.flower_type)
async def select_mono_flower(message: Message, state: FSMContext):
    await state.update_data(flower_type=message.text)
    await state.set_state(ProductCreationFSM.color)
    await message.answer("Выберите цвет букета:", reply_markup=get_color_keyboard())


# --- ОБЩИЙ ШАГ: ЦВЕТ -> ИНЛАЙН-ГАЛОЧКИ TILDA ---
@router.message(ProductCreationFSM.color)
async def select_color_and_show_tilda(message: Message, state: FSMContext):
    chosen_color = message.text
    data = await state.get_data()
    p_type = data.get("product_type", "author")

    if p_type == "mono":
        default_categories = ["Монобукеты"]
        flower_t = data.get("flower_type", "")
        if "Роза" in flower_t:
            default_categories.append("Розы")
    else:
        default_categories = ["Стиль REN"]

    await state.update_data(color=chosen_color, tilda_categories=default_categories)
    await state.set_state(ProductCreationFSM.tilda_categories)

    await message.answer(
        f"Цвет: <b>{chosen_color}</b>.\n"
        f"Отметьте галочками разделы для сайта Tilda (можно несколько):",
        reply_markup=get_tilda_categories_inline(default_categories),
        parse_mode="HTML"
    )


# --- ПЕРЕКЛЮЧЕНИЕ ГАЛОЧЕК БЕЗ НОВЫХ СООБЩЕНИЙ ---
@router.callback_query(ProductCreationFSM.tilda_categories, F.data.startswith("toggle_tilda:"))
async def toggle_tilda_section(callback: CallbackQuery, state: FSMContext):
    section_key = callback.data.split(":")[1]
    data = await state.get_data()
    selected = data.get("tilda_categories", [])

    if section_key in selected:
        selected.remove(section_key)
    else:
        selected.append(section_key)

    await state.update_data(tilda_categories=selected)
    await callback.message.edit_reply_markup(reply_markup=get_tilda_categories_inline(selected))
    await callback.answer()


# --- ПОДТВЕРЖДЕНИЕ -> ФИНАЛЬНАЯ ФОРМА ---
@router.callback_query(ProductCreationFSM.tilda_categories, F.data == "confirm_tilda_selection")
async def confirm_tilda_sections(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected_cats = data.get("tilda_categories", [])
    p_type = data.get("product_type")
    mode = data.get("sizes_mode")
    color = data.get("color")

    await state.set_state(ProductCreationFSM.waiting_for_upload)
    await callback.message.delete()

    if p_type == "mono":
        fl_name = data.get("flower_type")
        prompt = (
            f"✅ <b>Параметры зафиксированы:</b>\n"
            f"• Монобукет: {fl_name} ({color})\n"
            f"• Разделы Tilda: {'; '.join(selected_cats)}\n\n"
            f"📸 <b>Отправьте фото охапки (файлами без сжатия) и укажите в подписи:</b>\n"
            f"<code>Сорт / Цена за стебель / Штуки через запятую / Высота</code>\n\n"
            f"📝 <i>Пример:</i> <code>Охара / 350 / 9, 15, 25, 51 / 50</code>"
        )
    else:
        if mode == "single":
            size_hint = ("1 размер (для Tilda S/M/L рассчитаются позже при публикации на сайте)")
            fmt = "Название / Размер / Номер заказа / Высота*Ширина"
            example = "Розовый Шёлк / M / 567 / 45*35"
        else:
            size_hint = "несколько размеров (отдельный заказ МойСклада на каждый размер)"
            fmt = "Название / S заказ 150, M заказ 151, L заказ 152"
            example = "Розовый Шёлк / S заказ 150, M заказ 151, L заказ 152"
        prompt = (
            f"✅ <b>Параметры зафиксированы:</b>\n"
            f"• Авторский букет: {size_hint}\n"
            f"• Цвет: {color}\n"
            f"• Разделы Tilda: {'; '.join(selected_cats)}\n\n"
            f"📸 <b>Отправьте 6 фото (файлами) и видео (опционально)</b> с подписью:\n"
            f"<code>{fmt}</code>\n\n"
            f"📝 <i>Пример:</i> <code>{example}</code>"
        )

    await callback.message.answer(prompt, reply_markup=get_main_reply_keyboard(), parse_mode="HTML")
    await callback.answer()
