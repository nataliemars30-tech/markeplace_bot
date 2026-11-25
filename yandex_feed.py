"""YML-фид для Яндекс Бизнеса / Яндекс Карт.

Источник — ЖИВОЙ каталог Flowwow (все активные товары), кэш 1 час.
Новый товар, созданный ботом на Flowwow, сам попадает в фид → Яндекс Бизнес
подтягивает его по ссылке автоматически (источник «YML-фид» в кабинете).

Цены = как на сайте Tilda (base × 1.1, округление до десятков) — правило владельца.
SEO: названия/описания с ключами («купить букет», «доставка цветов», «Санкт-Петербург») —
поиск Карт идёт по товарам и влияет на позицию компании (справка Яндекс Бизнеса).
Правила Яндекс Бизнеса: в текстах НЕЛЬЗЯ ссылки и телефоны — вычищаем.
"""

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import httpx

from config import config

logger = logging.getLogger(__name__)

CACHE_TTL = 3600  # сек
_cache = {"ts": 0.0, "xml": ""}

EMOJI_RX = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️←-⇿⌀-⏿\U0001F1E6-\U0001F1FF‍]")
URL_RX = re.compile(r"https?://\S+|www\.\S+", re.I)
PHONE_RX = re.compile(r"[+8][\s(]?\d{3}[\s)\d-]{7,}")

CATS = {"1": "Авторские букеты", "2": "Монобукеты", "3": "Цветы поштучно",
        "4": "Вазы и кашпо", "5": "Композиции и декор"}

DECOR_WORDS = ("ваза", "кашпо", "кружка", "подсвечник")

# SEO-хвосты по типу товара (влияют на поиск по товарам в Картах)
SEO_TAIL = {
    "mono": "монобукет, купить в Санкт-Петербурге с доставкой",
    "author": "авторский букет от флориста, доставка цветов по СПб",
    "piece": "свежие цветы поштучно, Санкт-Петербург",
    "decor": "для букетов и интерьера, цветочная студия REN СПб",
}


def _clean(text: str) -> str:
    t = EMOJI_RX.sub("", text or "")
    t = URL_RX.sub("", t)
    t = PHONE_RX.sub("", t)
    return re.sub(r"[ \t]{2,}", " ", t).strip(" ,-–—\n")


def _category(p: Dict[str, Any]) -> str:
    name = (p.get("name") or "").lower()
    if any(w in name for w in DECOR_WORDS):
        return "4"
    if "моно" in name or re.search(r"\bиз \d+ ", name):
        return "2"
    if re.search(r"\b\d{1,2}\s*(шт|штук)\b", name) and "букет" not in name:
        return "3"
    return "1"


def _maps_price(fw_price: float) -> int:
    """Цена Карт = цена Tilda: base = fw/1.5 - 150; tilda = base × 1.1 (до десятков)."""
    base = fw_price / 1.5 - 150
    if base <= 0:
        return 0
    return int(round(base * 1.1 / 10) * 10)


def _seo_description(p: Dict[str, Any], cat: str) -> str:
    """Описание с ключевыми словами, без ссылок/телефонов (требование Яндекс Бизнеса)."""
    base = _clean(p.get("description") or "")
    # фирменный подвал Flowwow с ценами/доставкой/самовывозом оставляем только фактуру
    for marker in ("REN - азиатская эстетика", "Почему клиенты выбирают нашу студию"):
        i = base.find(marker)
        if i > 0:
            base = base[:i]
    key = {"1": "Купить авторский букет в Санкт-Петербурге с доставкой.",
           "2": "Купить монобукет в Санкт-Петербурге с доставкой за 90 минут.",
           "3": "Свежие цветы поштучно в Санкт-Петербурге.",
           "4": "Декор для букетов от цветочной студии REN в Санкт-Петербурге.",
           "5": "Авторские композиции от студии REN, Санкт-Петербург."}[cat]
    seo = (f"{key} Цветочная студия REN — азиатская эстетика в современных букетах: "
           f"свежие цветы утренней поставки, фирменная упаковка, фото букета перед отправкой.")
    full = (base + "\n\n" + seo).strip() if base else seo
    return full[:3000]


def build_yml_xml(products: List[Dict[str, Any]]) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [f'<?xml version="1.0" encoding="UTF-8"?>',
             f'<yml_catalog date="{now_str}"><shop>',
             "<name>Цветочная студия REN</name><company>REN Flowers</company>",
             "<url>https://ren-flowers.ru</url>",
             '<currencies><currency id="RUB" rate="1"/></currencies>',
             "<categories>"]
    for cid, cname in CATS.items():
        parts.append(f'<category id="{cid}">{escape(cname)}</category>')
    parts.append("</categories><offers>")
    n = 0
    for p in products:
        if not p.get("isActive") or p.get("isArchived"):
            continue
        price = _maps_price(float(p.get("price") or 0))
        if price <= 0:
            continue
        name = _clean(p.get("name") or "")[:250]
        if not name:
            continue
        cat = _category(p)
        images = [u for u in (p.get("images") or []) if u.startswith("http")]
        desc = _seo_description(p, cat)
        short = _clean(desc.split("\n\n")[0])[:250]
        url = (p.get("url") or "").strip()[:512]
        parts.append(f'<offer id="FW-{p.get("productId")}" available="true">')
        parts.append(f"<name>{escape(name)}</name><vendor>REN</vendor>")
        parts.append(f"<price>{price}</price><currencyId>RUB</currencyId>")
        parts.append(f"<categoryId>{cat}</categoryId>")
        if images:
            parts.append(f"<picture>{escape(images[0])}</picture>")
        parts.append(f"<description>{escape(desc)}</description>")
        parts.append(f"<shortDescription>{escape(short)}</shortDescription>")
        if url.startswith("http"):
            parts.append(f"<url>{escape(url)}</url>")
        parts.append("</offer>")
        n += 1
    parts.append("</offers></shop></yml_catalog>")
    logger.info(f"YML-фид Яндекс Бизнеса собран: {n} позиций")
    return "".join(parts)


async def fetch_flowwow_products() -> List[Dict[str, Any]]:
    items, seen, page = [], set(), 1
    async with httpx.AsyncClient(timeout=40) as cl:
        while page <= 12:
            r = await cl.post(
                f"https://apis.flowwow.com/apiseller/products?shopId={config.flowwow_shop_id}",
                headers={"Authorization": f"Bearer {config.flowwow_token}",
                         "Content-Type": "application/json"},
                json={"page": page, "limit": 50})
            batch = r.json().get("items") or []
            if not batch:
                break
            new = 0
            for p in batch:
                if p.get("productId") not in seen:
                    seen.add(p.get("productId"))
                    items.append(p)
                    new += 1
            if len(batch) < 50 or new == 0:
                break
            page += 1
    return items


async def get_feed_xml_live() -> str:
    """Фид «на лету» из Flowwow с кэшем 1 час. При сбое отдаём прошлую версию."""
    if _cache["xml"] and time.time() - _cache["ts"] < CACHE_TTL:
        return _cache["xml"]
    try:
        products = await fetch_flowwow_products()
        xml = build_yml_xml(products)
        if "<offer " in xml:
            _cache.update(ts=time.time(), xml=xml)
        return xml or _cache["xml"]
    except Exception as e:
        logger.error(f"Ошибка сборки YML-фида: {e}")
        return _cache["xml"] or '<?xml version="1.0" encoding="utf-8"?><yml_catalog date=""></yml_catalog>'
