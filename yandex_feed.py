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


def _sostav_text(p: Dict[str, Any]) -> str:
    """Состав из описания («Состав: А — 2 шт») или хвоста названия."""
    d = p.get("description") or ""
    m = re.search(r"Состав:\s*(.+?)(?:\.\s|\.$|$)", d, re.S)
    if m:
        return _clean(m.group(1).replace("\n", " "))[:400]
    n = p.get("name") or ""
    parts = re.split(r"\s[-–—]\s|\s+с\s+", n, maxsplit=1)
    if len(parts) > 1:
        return _clean(parts[1])[:400]
    return ""


def _enrich_name(p: Dict[str, Any], cat: str) -> str:
    """Название с использованием лимита 250: исходное + состав/SEO-хвост."""
    name = _clean(p.get("name") or "")
    if len(name) >= 200:
        return name[:250]
    tail = SEO_TAIL.get({"1": "author", "5": "author", "2": "mono", "3": "piece", "4": "decor"}.get(cat, "author"),
                        "доставка цветов по Санкт-Петербургу")
    cand = f"{name} — {tail}"
    return cand[:250] if len(cand) <= 250 else name


def _short_description(p: Dict[str, Any], cat: str) -> str:
    """Короткое описание ~250 знаков: лид + состав + УТП (лимит Карточки)."""
    base = _clean((p.get("description") or "").split("\n")[0])
    for marker in ("REN - азиатская", "Почему клиенты"):
        i = base.find(marker)
        if i > 0:
            base = base[:i]
    sostav = _sostav_text(p)
    parts = [base] if base else []
    if sostav:
        parts.append(f"Состав: {sostav}")
    parts.append("Свежие цветы утренней поставки, фирменная упаковка, доставка по СПб.")
    out = ". ".join(x.strip(" .") for x in parts if x.strip(" .")) + "."
    if len(out) > 250:
        out = out[:247].rsplit(" ", 1)[0].rstrip(" ,.;") + "..."
    return out


def _seo_description(p: Dict[str, Any], cat: str) -> str:
    """Полное описание ~2500-3000 знаков: уникальный текст + состав + уход + SEO-блок."""
    base = _clean(p.get("description") or "")
    for marker in ("REN - азиатская эстетика", "Почему клиенты выбирают нашу студию"):
        i = base.find(marker)
        if i > 0:
            base = base[:i]
    sostav = _sostav_text(p)
    key = {"1": "Купить авторский букет в Санкт-Петербурге с доставкой.",
           "2": "Купить монобукет в Санкт-Петербурге с доставкой за 90 минут.",
           "3": "Свежие цветы поштучно в Санкт-Петербурге с доставкой.",
           "4": "Декор для букетов от цветочной студии REN в Санкт-Петербурге.",
           "5": "Авторские композиции от студии REN, Санкт-Петербург."}[cat]
    blocks = [base] if base else []
    if sostav:
        blocks.append(f"Состав букета: {sostav}.")
    blocks.append(
        "Как ухаживать: перед постановкой в вазу обновите срез стеблей на 1–2 см под углом, "
        "используйте чистую прохладную воду и меняйте её каждые 1–2 дня. Держите букет вдали "
        "от прямого солнца, сквозняков и батарей — так цветы простоят дольше.")
    blocks.append(
        f"{key} Идеален на день рождения, День матери, юбилей, свидание, свадьбу, 8 Марта, "
        f"выписку из роддома или как тёплый комплимент без повода. Доставка цветов по "
        f"Санкт-Петербургу за 90 минут: привезём букет в защитной фирменной упаковке, "
        f"перед отправкой пришлём фото готового букета. Цветочная студия REN — азиатская "
        f"эстетика в современных букетах: свежие премиум-сорта утренней поставки, авторская "
        f"флористика, бережная сборка каждой композиции вручную. Закажите этот букет с "
        f"доставкой на дом или в офис — флорист соберёт его в день доставки, сохранив "
        f"настроение и палитру задумки.")
    full = "\n\n".join(b.strip() for b in blocks if b.strip())
    if len(full) > 3000:
        full = full[:2997].rsplit(" ", 1)[0].rstrip(" ,.;") + "..."
    return full


async def fetch_tilda_map() -> Dict[str, Dict[str, Any]]:
    """Каталог Tilda для ссылок в фиде: uid/цены/название. При сбое — пусто."""
    try:
        from tilda_mcp import TildaMCPClient
        cl = TildaMCPClient()
        import json as _json
        out, slice_ = [], 1
        while slice_ <= 5:
            raw = await cl.call_tool("store_get_products_list",
                                     {"projectid": int(getattr(config, "tilda_project_id", 13756635)),
                                      "slice": slice_, "slicesize": 100})
            d = _json.loads(raw)
            batch = d.get("products") or []
            if not batch:
                break
            out += batch
            if len(batch) < 100:
                break
            slice_ += 1
        return {p["uid"]: p for p in out if p.get("uid")}
    except Exception as e:
        logger.warning(f"Tilda map для фида недоступна: {e}")
        return {}


def _core(name: str) -> str:
    n = EMOJI_RX.sub("", (name or "").lower())
    n = re.sub(r"[«\"'](.*?)[»\"']", r" \1 ", n)
    n = re.sub(r"[^a-zа-я0-9 ]", " ", n)
    for w in ("букет", "монобукет", "авторский", "нежный", "светлый", "осенний", "размер", "шт"):
        n = n.replace(w, " ")
    return re.sub(r"\s{2,}", " ", n).strip()


def _quoted(name: str):
    m = re.search(r"[«\"]([^»\"]{3,40})[»\"]", name or "")
    return m.group(1).strip().lower() if m else None


def find_tilda_url(fw_name: str, maps_price: int, tmap: Dict[str, Dict[str, Any]]) -> str:
    """Ссылка на товар Tilda: по имени в «кавычках», иначе цена в диапазоне + ядро имени."""
    items = [t for t in tmap.values() if not (t.get("off") or "")]
    q = _quoted(fw_name)
    if q:
        for t in items:
            if _quoted(t.get("title") or "") == q:
                return f"https://ren-flowers.ru/tproduct/{t['uid']}"
    fc = _core(fw_name)
    best, best_len = "", 0
    for t in items:
        try:
            pmin = float(t.get("price_min") or t.get("price") or 0)
            pmax = float(t.get("price_max") or t.get("price") or pmin)
        except (TypeError, ValueError):
            continue
        if not (pmin <= maps_price <= pmax):
            continue
        tc = _core(t.get("title") or "")
        if tc and (tc in fc or fc in tc) and len(tc) > best_len:
            best, best_len = f"https://ren-flowers.ru/tproduct/{t['uid']}", len(tc)
    return best


def build_yml_xml(products: List[Dict[str, Any]], tmap: Dict[str, Dict[str, Any]] = None) -> str:
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
        cat = _category(p)
        name = _enrich_name(p, cat)
        if not name:
            continue
        images = [u for u in (p.get("images") or []) if u.startswith("http")]
        desc = _seo_description(p, cat)
        short = _short_description(p, cat)
        url = find_tilda_url(p.get("name") or "", price, tmap or {}) or (p.get("url") or "").strip()[:512]
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
        tmap = await fetch_tilda_map()
        xml = build_yml_xml(products, tmap)
        if "<offer " in xml:
            _cache.update(ts=time.time(), xml=xml)
        return xml or _cache["xml"]
    except Exception as e:
        logger.error(f"Ошибка сборки YML-фида: {e}")
        return _cache["xml"] or '<?xml version="1.0" encoding="utf-8"?><yml_catalog date=""></yml_catalog>'
