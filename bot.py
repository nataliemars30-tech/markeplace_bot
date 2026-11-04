"""Главный модуль Telegram-бота REN с пошаговым меню кнопками и интеграциями."""

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)

from config import config
from db import init_db, get_next_article, save_product_variation
from yandex_disk import YandexDiskClient, YandexDiskError
from moysklad_api import MoySkladClient, MoySkladAuthError, MoySkladAPIError
from flowwow_api import FlowwowClient, FlowwowAPIError
from platform_rules import (
    ROOT_CATEGORIES, TILDA_BOUQUET_CATEGORIES,
    get_flowwow_subcategory, get_okpd2_code, calculate_margin
)
from sheets_api import GoogleSheetsManager
from media_processor import mute_video_async

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ------------------ Фильтр безопасности ------------------
class WhitelistFilter(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        if not config.allowed_tg_ids:
            return True
        user_id = message.from_user.id
        if user_id not in config.allowed_tg_ids:
            logger.warning(f"Отклонен запрос от неавторизованного ID: {user_id}")
            return False
        return True


# ------------------ FSM Состояния мастера ------------------
class ProductWizard(StatesGroup):
    choosing_root_category = State()
    choosing_tilda_style = State()
    entering_name = State()
    entering_sizes_and_orders = State()
    waiting_for_files = State()
    confirming_publish = State()


bot = Bot(token=config.telegram_bot_token)
dp = Dispatcher(storage=MemoryStorage())

# Фильтр безопасности на ВСЕ входящие сообщения и колбэки
dp.message.filter(WhitelistFilter())
dp.callback_query.filter(WhitelistFilter())

yd_client = YandexDiskClient()
ms_client = MoySkladClient()
fw_client = FlowwowClient()
sheets_mgr = GoogleSheetsManager()

# Буферы для пачек файлов
media_group_buffers: Dict[str, List[types.Message]] = {}
media_group_locks: Dict[str, asyncio.Lock] = {}


def esc(text: Any) -> str:
    """Экранирование спецсимволов Markdown для пользовательского ввода."""
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", str(text))


# ------------------ Клавиатуры ------------------
def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Главная кнопка внизу экрана."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌸 Загрузить новый товар")],
            [KeyboardButton(text="❓ Помощь")]
        ],
        resize_keyboard=True
    )


def get_root_categories_kb() -> InlineKeyboardMarkup:
    """Выбор корневой категории."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💐 Букеты", callback_data="cat:Букеты"),
         InlineKeyboardButton(text="💍 Свадебные", callback_data="cat:Свадебные")],
        [InlineKeyboardButton(text="🎁 Композиции", callback_data="cat:Композиции"),
         InlineKeyboardButton(text="🕯 Декор / Доп", callback_data="cat:Декор")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="wizard:cancel")]
    ])


def get_tilda_styles_kb() -> InlineKeyboardMarkup:
    """Выбор стиля Tilda для букетов."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌿 В стиле REN", callback_data="style:В стиле REN"),
         InlineKeyboardButton(text="🌸 Нежный", callback_data="style:Нежный")],
        [InlineKeyboardButton(text="🌹 Розы", callback_data="style:Розы"),
         InlineKeyboardButton(text="✨ Премиум", callback_data="style:Премиум")],
        [InlineKeyboardButton(text="🍁 Сезонный", callback_data="style:Сезонные"),
         InlineKeyboardButton(text="⚪ Монобукет", callback_data="style:Моно")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="wizard:cancel")]
    ])


# ------------------ Хэндлеры команд и меню ------------------
@dp.message(Command("start"))
@dp.message(F.text == "❓ Помощь")
async def cmd_start(message: types.Message):
    await message.answer(
        "🌸 **Бот автоматизации витрин REN (Санкт-Петербург)**\n\n"
        "Нажмите кнопку **«🌸 Загрузить новый товар»** внизу экрана, чтобы запустить пошаговый мастер создания букета.",
        reply_markup=get_main_reply_keyboard(),
        parse_mode="Markdown"
    )


@dp.message(Command("new"))
@dp.message(F.text == "🌸 Загрузить новый товар")
async def start_wizard_button(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(ProductWizard.choosing_root_category)
    await message.answer(
        "**Шаг 1 из 5: Выберите категорию товара:**",
        reply_markup=get_root_categories_kb(),
        parse_mode="Markdown"
    )


@dp.callback_query(ProductWizard.choosing_root_category, F.data.startswith("cat:"))
async def process_cat_choice(callback: types.CallbackQuery, state: FSMContext):
    cat = callback.data.split(":")[1]
    await state.update_data(root_category=cat, is_mono=False)

    if cat == "Букеты":
        await state.set_state(ProductWizard.choosing_tilda_style)
        await callback.message.edit_text(
            f"Категория: **{cat}**\n\n**Шаг 2 из 5: Выберите стиль витрины:**",
            reply_markup=get_tilda_styles_kb(),
            parse_mode="Markdown"
        )
    else:
        await state.update_data(tilda_category=cat)
        await state.set_state(ProductWizard.entering_name)
        await callback.message.edit_text(
            f"Категория: **{cat}**\n\n**Шаг 2 из 5: Введите название позиции:**\n*(Например: `Композиция в коробке Нежность`)*",
            parse_mode="Markdown"
        )


@dp.callback_query(ProductWizard.choosing_tilda_style, F.data.startswith("style:"))
async def process_style_choice(callback: types.CallbackQuery, state: FSMContext):
    style = callback.data.split(":")[1]
    is_mono = (style == "Моно")
    actual_style = "В стиле REN" if is_mono else style

    await state.update_data(tilda_category=actual_style, is_mono=is_mono)
    await state.set_state(ProductWizard.entering_name)

    await callback.message.edit_text(
        f"Стиль: **{style}**\n\n**Шаг 3 из 5: Введите рабочее название букета:**\n*(Например: `Букет Нежность`)*",
        parse_mode="Markdown"
    )


@dp.message(ProductWizard.entering_name, F.text)
async def process_name_input(message: types.Message, state: FSMContext):
    name = message.text.strip()
    await state.update_data(name=name)
    await state.set_state(ProductWizard.entering_sizes_and_orders)

    await message.answer(
        f"Название: **{esc(name)}**\n\n"
        "**Шаг 4 из 5: Укажите размеры, цены и номера заказов МойСклада:**\n\n"
        "Примеры ввода:\n"
        "• Для одного размера:\n`M 4500 заказ 150`\n"
        "• Для линейки S/M/L:\n`S 3900 заказ 150, M 5600 заказ 151, L 8500 заказ 152`\n"
        "• Для моно (по штукам):\n`9 4200 заказ 160, 15 6800 заказ 161, 25 10500 заказ 162`",
        parse_mode="Markdown"
    )


@dp.message(ProductWizard.entering_sizes_and_orders, F.text)
async def process_sizes_input(message: types.Message, state: FSMContext):
    raw_text = message.text.strip()

    variations = []
    matches = re.findall(
        r"([A-Za-z0-9А-Яа-я]+)\s+(\d+)\s*(?:руб|₽)?\s*(?:заказ|зак\.?)?\s*([A-Za-z0-9-_]+)",
        raw_text, re.I
    )

    for label, price_str, order_no in matches:
        variations.append({
            "label": label.upper(),
            "price": float(price_str),
            "order_no": order_no
        })

    if not variations:
        await message.reply(
            "⚠️ Не удалось распознать формат. Попробуйте еще раз по шаблону:\n"
            "`M 4500 заказ 150` или `S 3500 заказ 150, M 5200 заказ 151`",
            parse_mode="Markdown"
        )
        return

    status_msg = await message.reply("⏳ Запрашиваю данные из МойСклада...")
    variations_with_cost = []
    try:
        for var in variations:
            order_data = await ms_client.get_customer_order_details(var["order_no"])
            cost = order_data["cost_price"]
            margin = calculate_margin(var["price"], cost)
            variations_with_cost.append({
                **var,
                "cost_price": cost,
                "margin_percent": margin,
                "positions": order_data["positions"]
            })
    except MoySkladAuthError as e:
        await status_msg.edit_text(f"❌ **Ошибка авторизации МойСклад:**\n{e}")
        return
    except MoySkladAPIError as e:
        await status_msg.edit_text(f"❌ **Ошибка МойСклад:**\n{e}")
        return

    await state.update_data(variations=variations_with_cost)
    await state.set_state(ProductWizard.waiting_for_files)

    vars_preview = "\n".join([
        f"• **{esc(v['label'])}**: Цена {v['price']:.0f}₽ | Себестоимость {v['cost_price']:.0f}₽ (Маржа {v['margin_percent']}%)"
        for v in variations_with_cost
    ])

    await status_msg.edit_text(
        f"✅ **Данные подтверждены:**\n{vars_preview}\n\n"
        "**Шаг 5 из 5: Прикрепите файлы букета:**\n"
        "📎 Отправьте через **«Файл» (без сжатия)** от 6 фотографий (первое фото — обложка) + видео (по желанию).",
        parse_mode="Markdown"
    )


# ------------------ Приём файлов через мастер ------------------
@dp.message(ProductWizard.waiting_for_files, F.document)
@dp.message(F.document)
async def handle_document_upload(message: types.Message, state: FSMContext):
    """Сбор альбома файлов без сжатия."""
    mg_id = message.media_group_id

    if not mg_id:
        await process_incoming_files([message], state)
        return

    if mg_id not in media_group_buffers:
        media_group_buffers[mg_id] = []
        media_group_locks[mg_id] = asyncio.Lock()

    async with media_group_locks[mg_id]:
        media_group_buffers[mg_id].append(message)

    await asyncio.sleep(1.2)

    async with media_group_locks[mg_id]:
        batch = media_group_buffers.pop(mg_id, None)

    if batch:
        await process_incoming_files(batch, state)


async def process_incoming_files(messages: List[types.Message], state: FSMContext):
    """Проверка и загрузка файлов."""
    current_state = await state.get_state()
    fsm_data = await state.get_data()

    photo_docs: List[types.Document] = []
    video_doc: Optional[types.Document] = None

    for m in messages:
        doc = m.document
        mime = (doc.mime_type or "").lower()
        if "video" in mime or doc.file_name.lower().endswith((".mp4", ".mov")):
            video_doc = doc
        elif "image" in mime or doc.file_name.lower().endswith((".jpg", ".jpeg", ".png", ".heic")):
            photo_docs.append(doc)

    if len(photo_docs) < 6:
        await messages[0].reply(
            f"⚠️ **Недостаточно фотографий!** Получено: {len(photo_docs)} из 6 требуемых.\n"
            "По регламенту REN для модерации необходимо отправить минимум 6 кадров."
        )
        return

    if current_state != ProductWizard.waiting_for_files:
        await messages[0].reply(
            "🌸 Сначала нажмите кнопку **«🌸 Загрузить новый товар»** внизу экрана, чтобы выбрать категорию и ввести цены.",
            reply_markup=get_main_reply_keyboard()
        )
        return

    await state.set_state(ProductWizard.confirming_publish)
    await state.update_data(
        photo_file_ids=[d.file_id for d in photo_docs],
        photo_names=[d.file_name for d in photo_docs],
        video_file_id=video_doc.file_id if video_doc else None,
        video_name=video_doc.file_name if video_doc else None
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚀 Создать и опубликовать", callback_data="wizard:publish"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="wizard:cancel")
        ]
    ])

    req_name = fsm_data.get("name")
    req_cat = fsm_data.get("root_category")
    await messages[0].reply(
        f"📸 Получено {len(photo_docs)} фото (1-е — обложка) + {'видео' if video_doc else 'без видео'}.\n\n"
        f"Товар: **{esc(req_name)}** ({esc(req_cat)})\n\n"
        f"Опубликовать карточки на Flowwow, Яндекс Диске и в Google Таблице?",
        reply_markup=kb,
        parse_mode="Markdown"
    )


# ------------------ Публикация на платформы ------------------
@dp.callback_query(ProductWizard.confirming_publish, F.data == "wizard:publish")
async def execute_publication(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)

    root_cat = data.get("root_category", "Букеты")
    name = data.get("name", "Букет REN")
    tilda_cat = data.get("tilda_category", "В стиле REN")
    is_mono = data.get("is_mono", False)
    variations = data.get("variations", [])
    photo_file_ids = data["photo_file_ids"]
    photo_names = data["photo_names"]
    video_file_id = data.get("video_file_id")
    video_name = data.get("video_name")

    base_article = get_next_article(root_cat)
    status_msg = await callback.message.answer(f"⏳ Регистрация артикула **{base_article}**...")

    temp_work_dir = config.temp_dir / base_article
    temp_work_dir.mkdir(parents=True, exist_ok=True)

    disk_base_folder = f"/REN_Каталог/{root_cat}/{base_article}"
    try:
        await yd_client.ensure_folder(disk_base_folder)
    except YandexDiskError as e:
        await status_msg.edit_text(f"❌ **Яндекс Диск:** {e}")
        return

    uploaded_photo_urls: List[str] = []
    try:
        for idx, (f_id, f_name) in enumerate(zip(photo_file_ids, photo_names), start=1):
            ext = Path(f_name).suffix or ".jpg"
            if ext.lower() == ".heic":
                ext = ".jpg"  # HEIC площадки не принимают
            local_filename = f"{idx:02d}_cover{ext}" if idx == 1 else f"{idx:02d}{ext}"
            local_path = temp_work_dir / local_filename

            file_obj = await bot.get_file(f_id)
            await bot.download_file(file_obj.file_path, destination=local_path)

            yd_path = f"{disk_base_folder}/{local_filename}"
            public_url = await yd_client.upload_file(local_path, yd_path)
            uploaded_photo_urls.append(public_url)
    except YandexDiskError as e:
        await status_msg.edit_text(f"❌ **Ошибка загрузки фото на Яндекс Диск:** {e}")
        return

    cover_url = uploaded_photo_urls[0]

    video_public_url = None
    if video_file_id and video_name:
        try:
            raw_v_path = temp_work_dir / "raw_video.mp4"
            muted_v_path = temp_work_dir / "video_muted.mp4"

            v_file_obj = await bot.get_file(video_file_id)
            await bot.download_file(v_file_obj.file_path, destination=raw_v_path)

            muted_ok = await mute_video_async(raw_v_path, muted_v_path)
            target_v = muted_v_path if muted_ok else raw_v_path
            video_public_url = await yd_client.upload_file(target_v, f"{disk_base_folder}/video_muted.mp4")
        except YandexDiskError as e:
            logger.warning(f"Видео не загружено (не критично): {e}")

    flowwow_subcat = get_flowwow_subcategory(root_cat, is_mono)
    okpd2_info = get_okpd2_code(name)

    fw_results = []
    for var in variations:
        full_art = f"{base_article}-{var['label']}"
        # Артикул НЕ включаем в название: он хранится в offerId (виден только кабинету)
        fw_name = f"{name} {var['label']}"

        # Состав из позиций заказа МойСклад
        consist = [
            {"name": p["name"], "quantity": int(p["qty"]), "measure": 1}
            for p in var.get("positions", [])
        ] or [{"name": name, "quantity": 1, "measure": 1}]
        composition_text = ", ".join(f"{p['name']} — {int(p['qty'])} шт" for p in var.get("positions", [])) or name

        fw_status = "создан"
        fw_id = "—"
        try:
            res = await fw_client.create_product(
                name=fw_name,
                price=var["price"],
                offer_id=full_art,
                sub_category_id=flowwow_subcat,
                photo_urls=uploaded_photo_urls,
                consist=consist,
                composition=composition_text,
                production_time=60
            )
            fw_id = res.get("id", "ok")
        except FlowwowAPIError as e:
            fw_status = f"ошибка ({e})"

        fw_results.append(f"{var['label']}: Flowwow {fw_status} (id {fw_id})")

        sheet_payload = {
            "full_article": full_art,
            "name": name,
            "variation": var["label"],
            "flowwow_id": fw_id,
            "price": var["price"],
            "cost_price": var["cost_price"],
            "margin_percent": var["margin_percent"],
            "moysklad_order_no": var["order_no"],
            "tilda_category": tilda_cat,
            "okpd2_code": okpd2_info["code"],
            "okpd2_name": okpd2_info["name"],
            "yandex_status": "в очереди",
            "cover_url": cover_url,
            "disk_folder_url": disk_base_folder,
            "created_by_tg_id": callback.from_user.id
        }
        sheets_ok = sheets_mgr.append_row(root_cat, sheet_payload)

        save_product_variation(
            base_article=base_article,
            full_article=full_art,
            root_category=root_cat,
            name=name,
            variation=var["label"],
            price=var["price"],
            cost_price=var["cost_price"],
            margin_percent=var["margin_percent"],
            moysklad_order_no=var["order_no"],
            flowwow_id=fw_id,
            tilda_category=tilda_cat,
            okpd2_code=okpd2_info["code"],
            cover_url=cover_url,
            all_photos_json=json.dumps(uploaded_photo_urls),
            video_url=video_public_url,
            created_by_tg_id=callback.from_user.id
        )

    shutil.rmtree(temp_work_dir, ignore_errors=True)

    fw_summary = "\n".join([f"• {esc(r)}" for r in fw_results])
    sheets_note = "" if sheets_ok else "\n⚠️ Запись в Google-таблицу не удалась (см. логи)"
    report_text = (
        f"✅ **Товар зарегистрирован! Артикул: {base_article}**\n\n"
        f"📁 **Яндекс Диск:** `{disk_base_folder}`\n"
        f"📑 **Google Таблица:** Лист «{root_cat}» обновлен{sheets_note}\n\n"
        f"🛍 **Маркетплейсы:**\n{fw_summary}\n"
        f"• **Яндекс Еда:** статус «в очереди» (ОКПД2: {okpd2_info['code']})"
    )

    await status_msg.edit_text(report_text, parse_mode="Markdown")


@dp.callback_query(F.data == "wizard:cancel")
async def cancel_wizard(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Создание товара отменено.")


# ------------------ Точка входа ------------------
async def main():
    logger.info("Инициализация локальной базы SQLite...")
    init_db()
    logger.info("Удаление старого вебхука (если был)...")
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Запуск бота...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
