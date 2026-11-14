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
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiohttp import web

from config import config
from db import init_db, get_next_article, save_product_variation, save_review, mark_review_answered, get_pending_reviews, get_review_draft, get_product_by_article
from yandex_disk import YandexDiskClient, YandexDiskError
from moysklad_api import MoySkladClient, MoySkladAuthError, MoySkladAPIError
from flowwow_api import FlowwowClient, FlowwowAPIError
from platform_rules import (
    get_flowwow_subcategory, get_okpd2_code, calculate_margin, calculate_platform_prices,
    generate_flowwow_description, count_stems, market_color_value_id, market_filter_color_id,
    generate_market_seo_description, display_positions, generate_flowwow_title,
    generate_tilda_seo_and_text, build_marketplace_title, clean_position_name
)
from seo_generator import generate_description
from sheets_api import GoogleSheetsManager
from media_processor import mute_video_async, compress_video_async
from market_api import YandexMarketClient, YandexMarketError
from eda_csv import EdaCsvManager, build_eda_row
from tilda_mcp import TildaCatalog
from states import ProductCreationFSM, HideFSM, SyncFSM
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
tilda_catalog = TildaCatalog()

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
    # ReplyKeyboardRemove принудительно сбрасывает закэшированную клиентом старую клавиатуру
    await message.answer("⌨️ Обновляю меню…", reply_markup=ReplyKeyboardRemove())
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


@dp.message(Command("purge"))
async def cmd_purge(message: types.Message, state: FSMContext):
    """Админ-команда очистки артикула везде: CSV Еды, Flowwow (архив), Маркет (удаление)."""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not re.fullmatch(r"REN(-[A-Z])?-\d{3}(-[A-Z0-9]+)?", parts[1].strip().upper()):
        await message.reply("Формат: /purge REN-009 или /purge REN-009-M")
        return
    article = parts[1].strip().upper()
    status = await message.answer(f"⏳ Очищаю {article}…")

    results = []
    # 1. CSV Еды: убрать строки с артикулом (по колонке «Название позиции», содержит артикул нельзя — ищем по совпадению в строке)
    csv_path = config.db_path.parent / "eda_catalog.csv"
    try:
        if csv_path.exists():
            lines = csv_path.read_text(encoding="utf-8-sig").splitlines()
            header, rows = lines[0], lines[1:]
            keep = [r for r in rows if article not in r]
            removed = len(rows) - len(keep)
            csv_path.write_text("\n".join([header] + keep), encoding="utf-8-sig")
            results.append(f"CSV Еды: удалено строк {removed}")
    except Exception as e:
        results.append(f"CSV Еды: ошибка ({e})")

    # 2. Flowwow: архив (архив — необратимый в «актив» только вручную, но товар сохраняется)
    try:
        await fw_client.archive_product(article)
        results.append("Flowwow: в архиве")
    except FlowwowAPIError as e:
        results.append(f"Flowwow: {e}")

    # 3. Маркет: удаление из кампании
    try:
        await market_client.delete_offer(article)
        results.append("Маркет: запрошено удаление")
    except YandexMarketError as e:
        results.append(f"Маркет: {e}")

    await status.edit_text(f"🧹 <b>{article}</b>\n" + "\n".join(f"• {esc(r)}" for r in results),
                           parse_mode="HTML")


# ------------------ Отзывы Flowwow: уведомления и ответы ------------------
REVIEWS_POLL_SECONDS = 1800  # поллинг раз в 30 минут (вебхуков у Flowwow нет)


def _stars(n: int) -> str:
    return "⭐" * max(1, min(5, int(n or 0)))


async def reviews_poller():
    """Фоновая задача: новые отзывы Flowwow -> уведомление с черновиком ответа.

    Черновик генерирует LLM (если задан OPENAI_API_KEY) или шаблон.
    Флорист согласует: /ok <id> отправляет черновик, /otvet <id> <текст> — свой вариант.
    Без подтверждения ничего не публикуется.
    """
    from review_replier import make_draft

    await asyncio.sleep(60)  # дать боту инициализироваться
    while True:
        try:
            items = await fw_client.get_reviews(page=1, per_page=30)
            for rev in items:
                if rev.get("answerShop"):
                    continue  # магазин уже ответил — не пристаём
                if not save_review(rev, draft=await make_draft(rev)):
                    continue  # уже уведомляли
                score = rev.get("shopScore", 0)
                text = (rev.get("text") or "").strip() or "— без текста —"
                author = rev.get("authorName") or "Аноним"
                draft = get_review_draft(rev["id"])
                msg = (
                    f"💬 <b>Новый отзыв на Flowwow!</b>\n\n"
                    f"{_stars(score)} <b>{score}/5</b> · {esc(author)} · {esc(rev.get('date', ''))}\n"
                    f"{esc(text[:600])}\n\n"
                    f"✍️ <b>Черновик ответа:</b>\n{esc(draft)}\n\n"
                    f"Отправить черновик: <code>/ok {rev['id']}</code>\n"
                    f"Своим текстом: <code>/otvet {rev['id']} ваш текст</code>"
                )
                for uid in config.allowed_tg_ids:
                    try:
                        await bot.send_message(uid, msg, parse_mode="HTML")
                    except Exception as e:
                        logger.warning(f"Не удалось отправить уведомление об отзыве {uid}: {e}")
            if items:
                logger.info(f"Отзывы: проверено {len(items)} свежих")
        except Exception as e:
            logger.warning(f"Поллер отзывов: {e}")
        await asyncio.sleep(REVIEWS_POLL_SECONDS)


SYNC_PLATFORMS = ("Маркет", "Еда", "Tilda")


def _sync_keyboard(selected: set) -> InlineKeyboardMarkup:
    rows = []
    for p in SYNC_PLATFORMS:
        mark = "✅" if p in selected else "⬜"
        rows.append([InlineKeyboardButton(text=f"{mark} {p}", callback_data=f"sync:tg:{p}")])
    rows.append([InlineKeyboardButton(text="🚀 Копировать", callback_data="sync:go"),
                 InlineKeyboardButton(text="Отмена", callback_data="sync:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(Command("sync"))
async def cmd_sync(message: types.Message, state: FSMContext):
    """Копирование существующего товара Flowwow на ВЫБРАННЫЕ площадки.
    Формат: /sync REN-011-M [цвет/гамма]"""
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not re.fullmatch(r"REN(-[A-Z])?-\d{3}(-[A-Z0-9]+)?", parts[1].strip().upper()):
        await message.reply("Формат: /sync REN-011-M [цвет]\n"
                            "Копирует товар, который есть на Flowwow, на выбранные площадки.")
        return
    article = parts[1].strip().upper()
    color = parts[2].strip() if len(parts) > 2 else "Разноцветный"
    status = await message.answer(f"⏳ Ищу {article} на Flowwow…")

    item = await fw_client.get_product(article)
    if not item:
        await status.edit_text(f"❌ {article} не найден на Flowwow. Проверьте артикул.")
        return

    # Базовая цена из цены Flowwow (обратная матрица: fw=(base+150)×1.5)
    try:
        base = int(round((float(item.get("price", 0)) / 1.5 - 150) / 10) * 10)
    except (TypeError, ValueError):
        base = 0
    if base <= 0:
        await status.edit_text(f"❌ Не смог вычислить базовую цену из цены Flowwow ({item.get('price')}).")
        return
    photos = [u for u in (item.get("images") or []) if u][:6]
    if not photos:
        await status.edit_text("❌ У товара на Flowwow нет фото — нечего копировать.")
        return

    m_art = re.match(r"(REN(-[A-Z])?-\d{3})(?:-([A-Z0-9]+))?$", article)
    base_article, label = m_art.group(1), (m_art.group(3) or "M")

    # Состав: consist карточки, иначе парсим из описания («Состав: А — 2 шт, Б — 1 шт.»)
    dpos = []
    for c in item.get("consist") or []:
        try:
            dpos.append({"name": clean_position_name(str(c.get("name", ""))), "qty": int(c.get("quantity", 1))})
        except (TypeError, ValueError):
            continue
    if not dpos:
        m_comp = re.search(r"Состав:\s*(.+?)(?:\.|$)", item.get("description") or "", re.S)
        if m_comp:
            for chunk in m_comp.group(1).split(","):
                m_q = re.match(r"(.+?)\s*—\s*(\d+)\s*шт", chunk.strip())
                if m_q:
                    dpos.append({"name": clean_position_name(m_q.group(1)), "qty": int(m_q.group(2))})
    if not dpos:
        dpos = [{"name": item.get("name", article), "qty": 1}]

    # Имя букета: из локальной базы, иначе из названия Flowwow («...«Имя»...»)
    record = get_product_by_article(article)
    bouquet_name = record["name"] if record else (item.get("name") or article)
    if not record:
        m_name = re.search(r"«([^»]+)»", item.get("name") or "")
        if m_name:
            bouquet_name = m_name.group(1)
    tilda_cats = [c.strip() for c in (record["tilda_category"] or "").split(";") if c.strip()] or ["Нежные"]

    height_cm = 45
    m_h = re.search(r"(\d{2,3})\s*см", item.get("description") or "")
    if m_h:
        height_cm = int(m_h.group(1))

    await state.update_data(sync={
        "article": article, "color": color, "base": base, "photos": photos,
        "label": label, "dpos": dpos, "bouquet_name": bouquet_name,
        "tilda_cats": tilda_cats, "height_cm": height_cm,
        "fw_name": item.get("name", article), "fw_price": item.get("price"),
        "selected": list(SYNC_PLATFORMS),
    })
    await state.set_state(SyncFSM.choosing)
    composition_text = ", ".join(f"{p['name']} — {int(p['qty'])} шт" for p in dpos)
    await status.edit_text(
        f"🔄 <b>{article}</b> — что копируем с Flowwow:\n\n"
        f"«{esc(item.get('name', article))}» · {item.get('price')}₽ → база {base}₽\n"
        f"Состав: {esc(composition_text)}\n"
        f"Фото: {len(photos)} шт · высота ≈ {height_cm} см · цвет: {esc(color)}\n\n"
        f"Снимите галочки с площадок, где товар УЖЕ есть:",
        reply_markup=_sync_keyboard(set(SYNC_PLATFORMS)),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("sync:"), SyncFSM.choosing)
async def sync_choose(callback: types.CallbackQuery, state: FSMContext):
    _, action, *rest = callback.data.split(":")
    data = (await state.get_data()).get("sync") or {}
    selected = set(data.get("selected") or [])

    if action == "cancel":
        await state.clear()
        await callback.message.edit_text("❌ Копирование отменено.")
        await callback.answer()
        return
    if action == "tg":
        platform = rest[0]
        if platform in selected:
            selected.discard(platform)
        else:
            selected.add(platform)
        data["selected"] = list(selected)
        await state.update_data(sync=data)
        await callback.message.edit_reply_markup(reply_markup=_sync_keyboard(selected))
        await callback.answer()
        return

    if action == "go":
        if not selected:
            await callback.answer("Выберите хотя бы одну площадку", show_alert=True)
            return
        await callback.answer("⏳ Копирую…")
        await state.clear()
        asyncio.create_task(_run_sync(callback.message, data, selected))


async def _run_sync(status_msg: types.Message, data: dict, selected: set) -> None:
    article = data["article"]
    color = data["color"]
    base = data["base"]
    prices = calculate_platform_prices(base)
    photos = data["photos"]
    label = data["label"]
    dpos = data["dpos"]
    bouquet_name = data["bouquet_name"]
    tilda_cats = data["tilda_cats"]
    height_cm = data["height_cm"]
    weight_g = {"S": 600, "M": 900, "L": 1400}.get(str(label).upper(), 900)
    is_mono = len({p["name"].split()[0].lower() for p in dpos}) == 1
    packaging_label = "Пленка"  # на Flowwow у REN букеты в упаковке по умолчанию

    composition_text = ", ".join(f"{p['name']} — {int(p['qty'])} шт" for p in dpos)
    fw_title = build_marketplace_title(bouquet_name, color, dpos)
    eda_desc = (f"Букет от цветочной студии REN, Санкт-Петербург. "
                f"Состав: {composition_text}. Аквабокс и кризал в комплекте.")

    results = [f"Источник: Flowwow «{esc(data['fw_name'])}», {data['fw_price']}₽ → база {base}₽"]

    if "Маркет" in selected:
        try:
            desc = await generate_description(bouquet_name, color, composition_text, packaging_label, "market")
            await market_client.create_card(
                offer_id=article,
                name=fw_title,
                price=prices["yandex_market"],
                pictures=photos,
                description=desc,
                composition=composition_text,
                total_flowers=count_stems(dpos),
                height_cm=height_cm,
                size_label="" if is_mono else label,
                weight_kg=round(weight_g / 1000, 2),
                diameter_cm=25,
                height_pack_cm=max(height_cm + 10, 40),
                okpd2="01.19.21.190", tnved="0603197000",
                packaging_film=True,
                color_value_id=market_color_value_id(color),
                bouquet_type="mono" if is_mono else "author",
                flower_names=[p["name"] for p in dpos],
                filter_color_id=market_filter_color_id(color),
            )
            results.append(f"✅ Маркет: карточка отправлена ({prices['yandex_market']}₽)")
        except YandexMarketError as e:
            results.append(f"⚠️ Маркет: {esc(str(e))[:120]}")

    if "Еда" in selected:
        try:
            eda_row = build_eda_row(name=bouquet_name, variation_label=label,
                                    is_mono=is_mono, price_eda=prices["yandex_eda"],
                                    positions=dpos, picture_urls=photos, description=eda_desc)
            if eda_csv.append_product(eda_row):
                results.append(f"✅ Еда: в CSV ({prices['yandex_eda']}₽)")
            else:
                results.append("⚠️ Еда: ошибка записи CSV")
        except Exception as e:
            results.append(f"⚠️ Еда: {esc(str(e))[:120]}")

    if "Tilda" in selected:
        try:
            tilda_seo = generate_tilda_seo_and_text(bouquet_name, dpos)
            tilda_status = await tilda_catalog.create_product(
                project_id=config.tilda_project_id,
                title=f"{bouquet_name} {label}",
                price_tilda=prices["tilda"],
                photo_urls=photos,
                tilda_categories=tilda_cats,
                description=eda_desc,
                full_article=article,
                photo_alts=[tilda_seo.get(f"alt_{i}", "") for i in range(1, 7)],
                seo_title=tilda_seo.get("title", ""),
                seo_descr=tilda_seo.get("description", ""),
                seo_keywords=tilda_seo.get("keywords", ""),
                height_cm=height_cm,
                weight_g=weight_g,
            )
            mark = "✅" if "проверен" in tilda_status else "⚠️"
            results.append(f"{mark} Tilda: {esc(tilda_status.splitlines()[0])}")
            if tilda_catalog.last_report:
                results.append("\n".join(tilda_catalog.last_report.splitlines()[1:]))
        except Exception as e:
            results.append(f"⚠️ Tilda: {esc(str(e))[:120]}")

    try:
        await status_msg.edit_text(f"🔄 <b>{article}</b> → {', '.join(sorted(selected))}\n" +
                                   "\n".join(results), parse_mode="HTML")
    except Exception:
        await status_msg.answer(f"🔄 <b>{article}</b>\n" + "\n".join(results), parse_mode="HTML")


@dp.message(Command("reload"))
async def cmd_reload_rules(message: types.Message):
    """Перечитать правила из листа «Правила» без перезапуска бота."""
    from rules_sheet import reload_rules
    rules = reload_rules()
    total = sum(len(v) for v in rules.values())
    await message.reply(f"✅ Правила перечитаны из таблицы: {total} значений в {len(rules)} разделах.")


@dp.message(Command("ok"))
async def cmd_ok(message: types.Message):
    """Согласовать и отправить черновик ответа: /ok <id>."""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.reply("Формат: /ok 11349849 — отправить подготовленный черновик")
        return
    review_id = int(parts[1].strip())
    draft = get_review_draft(review_id)
    if not draft:
        await message.reply(f"Черновик для отзыва {review_id} не найден. "
                            f"Ответьте вручную: /otvet {review_id} ваш текст")
        return
    status = await message.answer(f"⏳ Публикую ответ на отзыв {review_id}…")
    try:
        await fw_client.answer_review(review_id, draft)
        mark_review_answered(review_id, draft)
        await status.edit_text(f"✅ Ответ на отзыв {review_id} опубликован:\n{esc(draft)}")
    except FlowwowAPIError as e:
        await status.edit_text(f"❌ Flowwow не принял ответ: {esc(str(e))}")


@dp.message(Command("otvet"))
async def cmd_otvet(message: types.Message):
    """Ответ магазина на отзыв Flowwow: /otvet <id> <текст>.

    Без аргументов показывает список отзывов, ожидающих ответа.
    """
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2:
        pending = get_pending_reviews()
        if not pending:
            await message.reply("✅ Отзывов без ответа нет.")
            return
        lines = ["💬 <b>Отзывы без ответа:</b>\n"]
        for r in pending:
            snippet = (r["text"] or "— без текста —")[:120]
            lines.append(
                f"{_stars(r['shop_score'])} <code>{r['review_id']}</code> · {esc(r['author'] or 'Аноним')}: {esc(snippet)}"
            )
        lines.append("\nОтветить: /otvet &lt;id&gt; &lt;текст&gt;")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return
    if not parts[1].isdigit() or len(parts) < 3 or not parts[2].strip():
        await message.reply("Формат: /otvet 11349849 Спасибо за отзыв! Будем рады видеть вас снова 🌸")
        return
    review_id = int(parts[1])
    answer = parts[2].strip()
    status = await message.answer(f"⏳ Публикую ответ на отзыв {review_id}…")
    try:
        await fw_client.answer_review(review_id, answer)
        mark_review_answered(review_id, answer)
        await status.edit_text(f"✅ Ответ на отзыв {review_id} опубликован на Flowwow.")
    except FlowwowAPIError as e:
        await status.edit_text(f"❌ Flowwow не принял ответ: {esc(str(e))}")


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
            ready_v_path = temp_work_dir / "video_ready.mp4"
            v_file_obj = await bot.get_file(video_file_id)
            if (v_file_obj.file_size or 0) > 20 * 1024 * 1024:
                raise ValueError(
                    f"видео {v_file_obj.file_size / 1048576:.0f} МБ — Telegram не даёт боту скачать "
                    f"файлы больше 20 МБ. Сожмите видео (например, через «Экспорт» в мессенджере "
                    f"или любой компрессор до 720p) и отправьте заново."
                )
            await bot.download_file(v_file_obj.file_path, destination=raw_v_path)
            # Сжатие: H.264 до 1080p/CRF28 без звука — обычно в 3-10 раз легче оригинала
            compressed = await compress_video_async(raw_v_path, ready_v_path)
            target_v = ready_v_path if compressed else raw_v_path
            video_public_url = await yd_client.upload_file(target_v, f"{disk_base_folder}/video_muted.mp4")
        except Exception as e:
            logger.warning(f"Видео пропущено (не критично): {e}")
            video_note = (video_note + " " if video_note else "") + f"⚠️ Видео не загружено: {e}"

    flowwow_subcat = get_flowwow_subcategory(root_cat, is_mono)
    okpd2_info = get_okpd2_code(name)
    style_word = "нежных" if any(w in color for w in ("Неж", "Розов", "Крем", "Бел")) else "насыщенных"

    fw_results = []
    for var in variations:
        full_art = f"{base_article}-{clean_label(var['label'])}"
        # Чистый состав для покупателя: без упаковки/лент, с человекочитаемыми названиями
        dpos = display_positions(var.get("positions", []))
        # Название по ТЗ 07.11: авторские — формула «Букет [настроение] «Имя» с цветами» (≤100);
        # моно — прежний формат «N название»
        if is_mono:
            fw_name = generate_flowwow_title(name, is_mono, dpos, var["label"])
        else:
            fw_name = build_marketplace_title(name, color, dpos)
        platform_prices = calculate_platform_prices(var["base_price"])

        consist = [
            {"name": p["name"], "quantity": int(p["qty"]), "measure": 1}
            for p in dpos
        ] or [{"name": name, "quantity": 1, "measure": 1}]
        composition_text = ", ".join(f"{p['name']} — {int(p['qty'])} шт" for p in dpos) or name

        dims = var.get("dims") or ""
        m_dims = re.match(r"(\d+)\s*[*x×]\s*(\d+)", dims)
        if m_dims:
            var_height = int(m_dims.group(1))  # высота = стебли (первое число)
        elif is_mono:
            var_height = height_cm or 50
        else:
            var_height = {"S": 35, "M": 45, "L": 55}.get(clean_label(var["label"]), 45)
        width_cm = 25  # ширина букета по регламенту владельца

        # Упаковка из состава (нужна и Flowwow, и Маркету)
        pack_words = ("упаковк", "пленк", "плёнк", "тишью", "фоамиран", "корейк")
        has_packaging = any(
            any(w in str(p.get("name", "")).lower() for w in pack_words)
            for p in var.get("positions", [])
        )
        packaging_label = "Пленка" if has_packaging else "Под ленту"
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
                description=await generate_description(name, color, composition_text, packaging_label, "flowwow"),
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

        # --- Яндекс Маркет: полная карточка через business API ---
        stem_count = count_stems(var.get("positions", []))
        color_vid = market_color_value_id(color)
        filter_color_vid = market_filter_color_id(color)
        flower_names = [p.get("name", "") for p in dpos]
        weight_g = (max(300, int(var['label']) * 90) if is_mono and str(var['label']).isdigit()
                    else {"S": 600, "M": 900, "L": 1400}.get(str(var['label']).upper(), 900))
        seo_desc = await generate_description(name, color, composition_text, packaging_label, "market")
        await asyncio.sleep(1)  # дать файлам гарантированно осесть на диск перед скачиванием Маркетом
        try:
            await market_client.create_card(
                offer_id=full_art,
                name=fw_name,
                price=platform_prices["yandex_market"],
                pictures=uploaded_photo_urls,
                description=seo_desc,
                composition=composition_text,
                total_flowers=stem_count,
                height_cm=var_height,
                size_label="" if is_mono else var["label"],
                weight_kg=round(weight_g / 1000, 2),
                diameter_cm=width_cm,
                height_pack_cm=max(var_height + 10, 40),
                okpd2=okpd2_info.get("code", "01.19.21.190"),
                tnved=okpd2_info.get("tnved", "0603197000"),
                packaging_film=has_packaging,
                color_value_id=color_vid,
                bouquet_type="mono" if is_mono else "author",
                flower_names=flower_names,
                filter_color_id=filter_color_vid,
                video_url=video_public_url
            )
            market_status = "карточка отправлена (обработка)"
        except YandexMarketError as e:
            market_status = f"ошибка ({e})"

        eda_desc = (
            f"Букет от цветочной студии REN, Санкт-Петербург. "
            f"Состав: {composition_text}. Аквабокс и кризал в комплекте."
        )
        eda_row = build_eda_row(
            name=name, variation_label=var["label"], is_mono=is_mono,
            price_eda=platform_prices["yandex_eda"],
            positions=dpos,
            picture_urls=uploaded_photo_urls, description=eda_desc
        )
        eda_ok = eda_csv.append_product(eda_row)
        eda_status = "в CSV" if eda_ok else "ошибка записи CSV"

        # --- Tilda: прямое создание через MCP ---
        tilda_seo = generate_tilda_seo_and_text(name, dpos)
        tilda_status = await tilda_catalog.create_product(
            project_id=config.tilda_project_id,
            title=f"{name} {var['label']}",
            price_tilda=platform_prices["tilda"],
            photo_urls=uploaded_photo_urls,
            tilda_categories=tilda_cats,
            description=eda_desc,
            full_article=full_art,
            photo_alts=[tilda_seo.get(f"alt_{i}", "") for i in range(1, 7)],
            seo_title=tilda_seo.get("title", ""),
            seo_descr=tilda_seo.get("description", ""),
            seo_keywords=tilda_seo.get("keywords", ""),
            height_cm=var_height,
            weight_g=weight_g
        )

        fw_results.append(
            f"  ↳ Маркет: {market_status} | Еда: {eda_status} | Tilda: {tilda_status}"
        )
        if tilda_catalog.last_report:
            fw_results.append("\n".join(tilda_catalog.last_report.splitlines()[1:]))

        # --- Ссылка на карточку Flowwow для таблицы ---
        fw_url = ""
        if fw_id and fw_id != "—":
            try:
                fw_url = await fw_client.get_product_url(full_art) or ""
            except Exception:
                fw_url = ""

        sheet_payload = {
            "full_article": full_art,
            "name": name,
            "variation": var["label"],
            "flowwow_id": fw_id,
            "flowwow_url": fw_url,
            "price": var["base_price"],
            "flowwow_price": platform_prices["flowwow"],
            "eda_price": platform_prices["yandex_eda"],
            "market_price": platform_prices["yandex_market"],
            "tilda_price": platform_prices["tilda"],
            "cost_price": var.get("cost_price", 0),
            "margin_percent": var.get("margin_percent", 0),
            "moysklad_order_no": var.get("order_no", ""),
            "tilda_category": "; ".join(tilda_cats),
            "okpd2_code": okpd2_info["code"],
            "okpd2_name": okpd2_info["name"],
            "tnved": okpd2_info.get("tnved", ""),
            "consist": composition_text,
            "width": width_cm,
            "height": var_height,
            "status_flowwow": fw_status,
            "status_market": market_status,
            "status_eda": eda_status,
            "status_tilda": tilda_status,
            "yandex_status": "в очереди",
            "cover_url": cover_url,
            "all_photos": json.dumps(uploaded_photo_urls, ensure_ascii=False),
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


# ------------------ Глобальная защита: ни одна ошибка не проходит молча ------------------
@dp.error()
async def global_error_handler(event: types.ErrorEvent):
    logger.exception("Глобальная ошибка бота: %s", event.exception)
    target = None
    if event.update.message:
        target = event.update.message
    elif event.update.callback_query and event.update.callback_query.message:
        target = event.update.callback_query.message
    if target:
        try:
            await target.answer(
                "❌ <b>Сбой сети или сервиса</b> — процесс прерван, карточка может быть неполной.\n"
                "Подождите пару минут (у хостера бывают транзитные сбои DNS) и повторите создание товара.\n"
                "Если ошибка повторяется — пришлите скрин, разберём.",
                reply_markup=get_main_reply_keyboard(),
                parse_mode="HTML"
            )
        except Exception:
            pass
    return True


# ------------------ Точка входа ------------------
async def main():
    logger.info("Инициализация локальной базы SQLite...")
    init_db()
    logger.info("Загрузка правил из Google-таблицы (лист «Правила»)...")
    from rules_sheet import load_rules
    load_rules()
    logger.info("Запуск веб-сервера...")
    await start_photo_server()
    logger.info("Удаление старого вебхука...")
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Запуск поллера отзывов Flowwow...")
    if config.reviews_enabled.lower() in ("on", "1", "yes", "true", "вкл"):
        asyncio.create_task(reviews_poller())
        logger.info("Поллер отзывов ВКЛЮЧЁН")
    else:
        logger.info("Поллер отзывов ОТКЛЮЧЁН (REVIEWS_ENABLED=off)")
    logger.info("Запуск бота...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
