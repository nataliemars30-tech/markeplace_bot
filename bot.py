"""Главный модуль Telegram-бота REN. Меню v2 + конвейер публикации."""

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiohttp import web

from config import config
from db import init_db, get_next_article, save_product_variation
from yandex_disk import YandexDiskClient, YandexDiskError
from moysklad_api import MoySkladClient, MoySkladAuthError, MoySkladAPIError
from flowwow_api import FlowwowClient, FlowwowAPIError
from platform_rules import (
    get_flowwow_subcategory, get_okpd2_code, calculate_margin, calculate_platform_prices,
    generate_flowwow_description
)
from sheets_api import GoogleSheetsManager
from media_processor import mute_video_async
from market_api import YandexMarketClient, YandexMarketError
from eda_csv import EdaCsvManager, build_eda_row
from states import ProductCreationFSM, HideFSM
from keyboards import get_main_reply_keyboard
import handlers_menu

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


bot = Bot(token=config.telegram_bot_token)
dp = Dispatcher(storage=MemoryStorage())
dp.message.filter(WhitelistFilter())
dp.callback_query.filter(WhitelistFilter())
dp.include_router(handlers_menu.router)

yd_client = YandexDiskClient()
ms_client = MoySkladClient()
fw_client = FlowwowClient()
sheets_mgr = GoogleSheetsManager()
market_client = YandexMarketClient()
eda_csv = EdaCsvManager(config.db_path.parent / "eda_catalog.csv")

media_group_buffers: Dict[str, List[types.Message]] = {}
media_group_locks: Dict[str, asyncio.Lock] = {}


def esc(text: Any) -> str:
    """Экранирование спецсимволов Markdown для пользовательского ввода."""
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", str(text))


def clean_label(label: str) -> str:
    """Метка размера для offerId: только латиница и цифры (правила Flowwow)."""
    lab = re.sub(r"[^A-Za-z0-9]", "", str(label).upper())
    return lab or "M"


def get_public_base_url() -> str:
    explicit = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if domain:
        return f"https://{domain}"
    return ""


async def start_photo_server() -> None:
    """HTTP-сервер: /photos/<артикул>/<файл> — стабильные ссылки для площадок;
    /feed.xml — YML-фид для Яндекс Карт и Tilda; /eda.csv — CSV для импорта в Еду."""
    from yandex_feed import get_feed_xml

    async def eda_csv_handler(request: web.Request) -> web.Response:
        csv_path = config.db_path.parent / "eda_catalog.csv"
        if not csv_path.exists():
            return web.Response(text="CSV еще пуст - создайте товар через бота", status=404)
        return web.Response(
            body=csv_path.read_bytes(),
            content_type="text/csv",
            charset="utf-8",
            headers={"Content-Disposition": "attachment; filename=eda_catalog.csv"}
        )

    photos_root = config.db_path.parent / "photos"
    photos_root.mkdir(parents=True, exist_ok=True)
    app = web.Application()
    app.router.add_static("/photos/", photos_root, show_index=False)
    app.router.add_get("/feed.xml", lambda r: web.Response(text=get_feed_xml(), content_type="text/xml"))
    app.router.add_get("/eda.csv", eda_csv_handler)
    app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info(f"Фото-сервер, YML-фид и CSV Еды запущены на порту {port}")


# ------------------ Команды ------------------
@dp.message(Command("start"))
@dp.message(F.text == "❓ Помощь")
async def cmd_start(message: types.Message):
    await message.answer(
        "🌸 <b>Бот автоматизации витрин REN</b>\n\n"
        "Используйте кнопки внизу экрана. Все промежуточные выборы делаются в меню клавиатуры, "
        "в чат уходит только финальная форма под фото.",
        reply_markup=get_main_reply_keyboard(),
        parse_mode="HTML"
    )


@dp.message(Command("new"))
async def cmd_new(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=get_main_reply_keyboard())


# ------------------ Скрытие букета со всех витрин ------------------
@dp.message(HideFSM.waiting_article, F.text)
async def hide_by_article(message: types.Message, state: FSMContext):
    from flowwow_api import FlowwowClient as _FW
    article = message.text.strip().upper()
    if not re.fullmatch(r"REN(-[A-Z])?-\d{3}(-[A-Z0-9]+)?", article):
        await message.reply("Не похоже на артикул. Формат: REN-042 или REN-042-M")
        return
    await state.clear()
    status = await message.answer(f"⏳ Скрываю {article} на Flowwow...")
    try:
        await fw_client.hide_product(article)
        fw_result = "скрыт"
    except FlowwowAPIError as e:
        fw_result = f"ошибка ({e})"
    await status.edit_text(
        f"🔒 <b>{article}</b>\n"
        f"• Flowwow: {esc(fw_result)}\n"
        f"• Еда / Маркет: нет API скрытия — помечено для Claw/ручного снятия в ЛК",
        parse_mode="Markdown"
    )


# ------------------ Приём файлов и подписи ------------------
@dp.message(ProductCreationFSM.waiting_for_upload, F.document)
@dp.message(F.document)
async def handle_document_upload(message: types.Message, state: FSMContext):
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


def extract_caption(messages: List[types.Message]) -> str:
    """Подпись/текст из пачки сообщений (caption может быть на любом элементе)."""
    for m in messages:
        if m.caption and m.caption.strip():
            return m.caption.strip()
        if m.text and m.text.strip():
            return m.text.strip()
    return ""


async def process_incoming_files(messages: List[types.Message], state: FSMContext):
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
            f"⚠️ <b>Недостаточно фотографий!</b> Получено: {len(photo_docs)} из 6.\n"
            "Отправьте файлы без сжатия, минимум 6 кадров.",
            parse_mode="HTML"
        )
        return

    if current_state != ProductCreationFSM.waiting_for_upload.state:
        await messages[0].reply(
            "🌸 Начните с кнопок внизу экрана, затем отправьте фото с подписью по шаблону.",
            reply_markup=get_main_reply_keyboard()
        )
        return

    caption = extract_caption(messages)
    p_type = fsm_data.get("product_type")

    # --- Парсинг подписи ---
    if p_type == "mono":
        parsed = parse_mono_caption(caption, fsm_data.get("flower_type", "Цветы"))
        if not parsed:
            await messages[0].reply(
                "⚠️ Не разобрала подпись. Формат:\n"
                "<code>Сорт / Цена за стебель / Штуки / Высота</code>\n"
                "Пример: <code>Охара / 350 / 9, 15, 25, 51 / 50</code>",
                parse_mode="HTML"
            )
            return
        product_name, variations, height_cm = parsed
    else:
        parsed = parse_author_caption(caption, fsm_data.get("sizes_mode", "single"))
        if not parsed:
            await messages[0].reply(
                "⚠️ Не разобрала подпись. Форматы:\n"
                "<code>Название / M / 567 / 45*35</code>\n"
                "или <code>Название / S заказ 150, M заказ 151</code>",
                parse_mode="HTML"
            )
            return
        product_name, variations = parsed
        height_cm = 0

    # --- Разрешение заказов МойСклад (авторские) ---
    if p_type != "mono":
        status_msg = await messages[0].reply("⏳ Запрашиваю данные из МойСклада...")
        try:
            for var in variations:
                order_no = var.get("order_no", "")
                if not order_no:
                    continue
                order_data = await ms_client.get_customer_order_details(order_no)
                sell = order_data["selling_sum"]
                cost = order_data["cost_price"]
                var["positions"] = order_data["positions"]
                var["base_price"] = var.get("entered_price") or sell
                var["cost_price"] = cost
                var["margin_percent"] = calculate_margin(var["base_price"], cost) if cost else 0.0
        except MoySkladAuthError as e:
            await status_msg.edit_text(f"❌ <b>Ошибка авторизации МойСклад:</b>\n{e}", parse_mode="HTML")
            return
        except MoySkladAPIError as e:
            await status_msg.edit_text(f"❌ <b>Ошибка МойСклад:</b>\n{e}", parse_mode="HTML")
            return

    await state.update_data(
        name=product_name,
        variations=variations,
        photo_file_ids=[d.file_id for d in photo_docs],
        photo_names=[d.file_name for d in photo_docs],
        video_file_id=video_doc.file_id if video_doc else None,
        video_name=video_doc.file_name if video_doc else None,
        height_cm=height_cm,
    )

    # Лимит Telegram Bot API: скачивание файлов > 20 МБ недоступно
    fsm_data = await state.get_data()
    video_note = ""
    if video_doc and (video_doc.file_size or 0) > 20 * 1024 * 1024:
        video_note = (f"⚠️ Видео ({video_doc.file_size / 1048576:.0f} МБ) пропущено: лимит Telegram 20 МБ. "
                      "Сожмите видео или загрузите на Диск вручную.")
    await state.update_data(video_note=video_note)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Опубликовать", callback_data="wizard:publish"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="wizard:cancel")
    ]])
    sizes_preview = ", ".join(v["label"] for v in variations)
    await messages[0].reply(
        f"📸 Фото: {len(photo_docs)} (+ {'видео' if video_doc else 'без видео'})\n"
        f"Товар: <b>{esc(product_name)}</b> · размеры: {esc(sizes_preview)}\n\n"
        f"Опубликовать на Flowwow, Маркете, в CSV Еды, на Диске и в таблице?"
        + (f"\n\n{video_note}" if video_note else ""),
        reply_markup=kb, parse_mode="HTML"
    )


def parse_mono_caption(caption: str, flower_raw: str):
    """'Охара / 350 / 9, 15, 25, 51 / 50' -> (имя, вариации, высота)."""
    parts = [p.strip() for p in caption.split("/")]
    if len(parts) < 3:
        return None
    sort_name, stem_str, counts_str = parts[0], parts[1], parts[2]
    if not sort_name or not re.fullmatch(r"\d+(\.\d+)?", stem_str):
        return None
    counts = re.findall(r"\d+", counts_str)
    if not counts:
        return None
    flower = re.sub(r"[^\w\s()-]", "", flower_raw).strip()
    flower = re.sub(r"^(🌹|🌸|🌿|⚪️|🌺|🌷|🪻|✨)\s*", "", flower).strip() or "Цветы"
    height = int(re.search(r"\d+", parts[3]).group()) if len(parts) > 3 and re.search(r"\d+", parts[3]) else 0
    stem_price = float(stem_str)
    product_name = f"{flower} {sort_name}".strip()
    variations = []
    for c in counts:
        qty = int(c)
        base = stem_price * qty
        variations.append({
            "label": str(qty),
            "base_price": base,
            "cost_price": base,
            "margin_percent": 0.0,
            "order_no": "",
            "positions": [{"name": product_name, "qty": qty, "buy_price": stem_price}]
        })
    return product_name, variations, height


def parse_author_caption(caption: str, sizes_mode: str):
    """single: 'Название / M / 567 / 45*35' | multi: 'Название / S заказ 150, M заказ 151'."""
    parts = [p.strip() for p in caption.split("/")]
    if len(parts) < 2:
        return None
    name = parts[0]
    if not name:
        return None
    variations = []
    rest = "/".join(parts[1:])
    if "заказ" in rest.lower():
        found = re.findall(
            r"([A-Za-z0-9А-Яа-я]+)\s+(?:заказ|зак\.?)\s*([A-Za-z0-9-_]+)", rest, re.I)
        for label, order_no in found:
            lab = clean_label(label)
            if lab in ("M",) and label.upper() not in ("M",):
                pass
            variations.append({"label": lab, "order_no": order_no,
                               "entered_price": None, "dims": ""})
    else:
        try:
            size = clean_label(parts[1])
            order_no = parts[2]
            dims = parts[3] if len(parts) > 3 else ""
        except IndexError:
            return None
        variations.append({"label": size, "order_no": order_no,
                           "entered_price": None, "dims": dims})
    if not variations:
        return None
    return name, variations


# ------------------ Публикация ------------------
@dp.callback_query(F.data == "wizard:publish")
async def execute_publication(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)

    root_cat = data.get("root_category", "Букеты")
    name = data.get("name", "Букет REN")
    p_type = data.get("product_type", "author")
    is_mono = p_type == "mono"
    color = data.get("color", "")
    tilda_cats = data.get("tilda_categories", [])
    variations = data.get("variations", [])
    photo_file_ids = data["photo_file_ids"]
    photo_names = data["photo_names"]
    video_file_id = data.get("video_file_id")
    video_name = data.get("video_name")
    height_cm = int(data.get("height_cm") or 0)

    base_article = get_next_article(root_cat)
    status_msg = await callback.message.answer(f"⏳ Регистрация артикула <b>{base_article}</b>...")

    temp_work_dir = config.temp_dir / base_article
    temp_work_dir.mkdir(parents=True, exist_ok=True)

    disk_base_folder = f"/REN_Каталог/{root_cat}/{base_article}"
    try:
        await yd_client.ensure_folder(disk_base_folder)
    except YandexDiskError as e:
        await status_msg.edit_text(f"❌ <b>Яндекс Диск:</b> {e}", parse_mode="HTML")
        return

    uploaded_photo_urls: List[str] = []
    photos_dir = config.db_path.parent / "photos" / base_article
    photos_dir.mkdir(parents=True, exist_ok=True)
    public_base = get_public_base_url()
    if not public_base:
        logger.warning("PUBLIC_BASE_URL/RAILWAY_PUBLIC_DOMAIN не заданы — Flowwow получит ссылки Диска")
    try:
        for idx, (f_id, f_name) in enumerate(zip(photo_file_ids, photo_names), start=1):
            ext = Path(f_name).suffix or ".jpg"
            if ext.lower() == ".heic":
                ext = ".jpg"
            local_filename = f"{idx:02d}_cover{ext}" if idx == 1 else f"{idx:02d}{ext}"
            local_path = temp_work_dir / local_filename

            file_obj = await bot.get_file(f_id)
            await bot.download_file(file_obj.file_path, destination=local_path)

            shutil.copy2(local_path, photos_dir / local_filename)
            stable_url = f"{public_base}/photos/{base_article}/{local_filename}" if public_base else None

            yd_path = f"{disk_base_folder}/{local_filename}"
            disk_url = await yd_client.upload_file(local_path, yd_path)
            uploaded_photo_urls.append(stable_url or disk_url)
    except YandexDiskError as e:
        await status_msg.edit_text(f"❌ <b>Ошибка загрузки фото на Яндекс Диск:</b> {e}", parse_mode="HTML")
        return

    cover_url = uploaded_photo_urls[0]

    video_public_url = None
    video_note = data.get("video_note", "")
    if video_file_id and video_name:
        try:
            raw_v_path = temp_work_dir / "raw_video.mp4"
            muted_v_path = temp_work_dir / "video_muted.mp4"
            v_file_obj = await bot.get_file(video_file_id)
            if (v_file_obj.file_size or 0) > 20 * 1024 * 1024:
                raise ValueError(f"Видео {v_file_obj.file_size / 1048576:.0f} МБ превышает лимит Telegram 20 МБ")
            await bot.download_file(v_file_obj.file_path, destination=raw_v_path)
            muted_ok = await mute_video_async(raw_v_path, muted_v_path)
            target_v = muted_v_path if muted_ok else raw_v_path
            video_public_url = await yd_client.upload_file(target_v, f"{disk_base_folder}/video_muted.mp4")
        except Exception as e:
            logger.warning(f"Видео пропущено (не критично): {e}")

    flowwow_subcat = get_flowwow_subcategory(root_cat, is_mono)
    okpd2_info = get_okpd2_code(name)
    style_word = "нежных" if any(w in color for w in ("Неж", "Розов", "Крем", "Бел")) else "насыщенных"

    fw_results = []
    for var in variations:
        full_art = f"{base_article}-{clean_label(var['label'])}"
        fw_name = f"{name} {var['label']}"
        platform_prices = calculate_platform_prices(var["base_price"])

        consist = [
            {"name": p["name"], "quantity": int(p["qty"]), "measure": 1}
            for p in var.get("positions", [])
        ] or [{"name": name, "quantity": 1, "measure": 1}]
        composition_text = ", ".join(f"{p['name']} — {int(p['qty'])} шт" for p in var.get("positions", [])) or name

        dims = var.get("dims") or ""
        m_dims = re.match(r"(\d+)\s*[*x×]\s*(\d+)", dims)
        if m_dims:
            width_cm, var_height = int(m_dims.group(2)), int(m_dims.group(1))
        elif is_mono:
            var_height, width_cm = (height_cm or 50), 30
        else:
            width_cm, var_height = {"S": (25, 35), "M": (35, 45), "L": (45, 55)}.get(
                clean_label(var["label"]), (35, 45))

        fw_status = "создан"
        fw_id = "—"
        try:
            res = await fw_client.create_product(
                name=fw_name,
                price=platform_prices["flowwow"],
                offer_id=full_art,
                sub_category_id=flowwow_subcat,
                photo_urls=uploaded_photo_urls,
                consist=consist,
                composition=composition_text,
                description=generate_flowwow_description(name, var.get("positions", []), style_word),
                production_time=5,
                width_cm=width_cm,
                height_cm=var_height
            )
            fw_id = res.get("id", "ok")
        except FlowwowAPIError as e:
            fw_status = f"ошибка ({e})"

        fw_results.append(
            f"{var['label']}: база {var['base_price']:.0f}₽ → Flowwow {platform_prices['flowwow']}₽ ({fw_status}, id {fw_id})"
        )

        try:
            await market_client.create_offer(
                offer_id=full_art,
                name=fw_name,
                price=platform_prices["yandex_market"],
                picture_urls=uploaded_photo_urls,
                weight_kg=round(900 / 1000, 2)
            )
            market_status = "создан"
        except YandexMarketError as e:
            market_status = f"ошибка ({e})"

        eda_desc = (
            f"Букет от цветочной студии REN, Санкт-Петербург. "
            f"Состав: {composition_text}. Аквабокс, открытка и кризал в комплекте."
        )
        eda_row = build_eda_row(
            name=name, variation_label=var["label"], is_mono=is_mono,
            price_eda=platform_prices["yandex_eda"],
            positions=var.get("positions", []),
            cover_url=cover_url, description=eda_desc
        )
        eda_ok = eda_csv.append_product(eda_row)
        eda_status = "в CSV" if eda_ok else "ошибка записи CSV"

        fw_results.append(f"  ↳ Маркет: {market_status} | Еда: {eda_status} (цена {platform_prices['yandex_eda']}₽)")

        sheet_payload = {
            "full_article": full_art,
            "name": name,
            "variation": var["label"],
            "flowwow_id": fw_id,
            "price": var["base_price"],
            "cost_price": var.get("cost_price", 0),
            "margin_percent": var.get("margin_percent", 0),
            "moysklad_order_no": var.get("order_no", ""),
            "tilda_category": "; ".join(tilda_cats),
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
            price=var["base_price"],
            cost_price=var.get("cost_price", 0),
            margin_percent=var.get("margin_percent", 0),
            moysklad_order_no=var.get("order_no", ""),
            flowwow_id=fw_id,
            tilda_category="; ".join(tilda_cats),
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
        f"✅ <b>Товар зарегистрирован! Артикул: {base_article}</b>\n\n"
        f"📁 Яндекс Диск: <code>{disk_base_folder}</code>\n"
        f"📑 Таблица: лист «{esc(root_cat)}» обновлен{sheets_note}\n\n"
        f"🛍 <b>Площадки:</b>\n{fw_summary}\n"
        f"• Цвет: {esc(color)} · Разделы Tilda: {esc('; '.join(tilda_cats))}\n"
        + (f"\n{video_note}" if video_note else "")
    )
    await status_msg.edit_text(report_text, parse_mode="HTML")


@dp.callback_query(F.data == "wizard:cancel")
async def cancel_wizard(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Создание товара отменено.")


# ------------------ Точка входа ------------------
async def main():
    logger.info("Инициализация локальной базы SQLite...")
    init_db()
    logger.info("Запуск веб-сервера...")
    await start_photo_server()
    logger.info("Удаление старого вебхука...")
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Запуск бота...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
