"""Модуль правил: ценообразование по витринам, классификация, генерация контента.

Совмещает ТЗ-матрицу (05.11.2026) с рабочей версией бота.
Источник истины по допустимым полям карточки Flowwow: /categories/subCategoryProperties.
"""

from typing import Dict, Any, List


ROOT_CATEGORIES = ["Букеты", "Свадебные", "Композиции", "Декор", "Доп. товары"]

# Разделы каталога Tilda (множественный выбор, через ";")
TILDA_BOUQUET_CATEGORIES = {
    "Нежный": "nezhnij",
    "В стиле REN": "ren-style",
    "Розы": "roses",
    "Премиум": "premium",
    "Сезонные": "seasonal",
    "Свадебные": "wedding"
}
# Алиас из Мастер-ТЗ (обратная совместимость)
TILDA_CATEGORIES_LIST = list(TILDA_BOUQUET_CATEGORIES.keys())

YANDEX_OKPD2_MAP = {
    "роза": {"okpd2": "011921110", "tnved": "0603110000", "name": "Розы срезанные"},
    "гвоздика": {"okpd2": "011921120", "tnved": "0603120000", "name": "Гвоздики срезанные"},
    "орхидея": {"okpd2": "011921130", "tnved": "0603130000", "name": "Орхидеи срезанные"},
    "гладиолус": {"okpd2": "011921140", "tnved": "0603150000", "name": "Гладиолусы срезанные"},
    "хризантема": {"okpd2": "011921150", "tnved": "0603140000", "name": "Хризантемы срезанные"},
    "прочие": {"okpd2": "011921190", "tnved": "0603197000", "name": "Цветы срезанные прочие"}
}


# ------------------ Матрица ценообразования (Мастер-ТЗ) ------------------
def calculate_platform_prices(base_price: float) -> Dict[str, int]:
    """
    От базовой цены (себестоимость/основа из МойСклада):
      Tilda +10% | Яндекс Карты = Tilda | Flowwow (base+150)*1.5 | Еда +30% | Маркет +50%
    """
    base = float(base_price)
    price_tilda = int(round(base * 1.10))
    return {
        "base": int(round(base)),
        "tilda": price_tilda,
        "flowwow": int(round((base + 150.0) * 1.50)),
        "yandex_eda": int(round(base * 1.30)),
        "yandex_market": int(round(base * 1.50)),
        "yandex_maps": price_tilda
    }


# ------------------ Классификация ------------------
def get_okpd2_code(name: str) -> Dict[str, str]:
    """ОКПД2 по названию (обёртка над картой ТЗ)."""
    n = (name or "").lower()
    for key, val in YANDEX_OKPD2_MAP.items():
        if key != "прочие" and key in n:
            return {"code": val["okpd2"], "name": val["name"], "tnved": val["tnved"]}
    d = YANDEX_OKPD2_MAP["прочие"]
    return {"code": d["okpd2"], "name": d["name"], "tnved": d["tnved"]}


def get_flowwow_subcategory(root_cat: str, is_mono: bool, sub_type: str = "") -> int:
    """
    subCategoryId Flowwow:
      1 Монобукеты | 2 Букеты (сборные/свадебные) | 3 Коробка | 4 Корзина | 5 Композиции | 7 Декор
    Полная версия (не упрощённая из ТЗ — сохранены корзины/коробки/декор).
    """
    if root_cat == "Букеты":
        return 1 if is_mono else 2
    elif root_cat == "Свадебные":
        return 2
    elif root_cat == "Композиции":
        st = (sub_type or "").lower()
        if "коробк" in st:
            return 3
        elif "корзин" in st:
            return 4
        return 5
    elif root_cat == "Декор":
        return 7
    return 2


def calculate_margin(selling_price: float, cost_price: float) -> float:
    if selling_price <= 0:
        return 0.0
    return round(((selling_price - cost_price) / selling_price) * 100.0, 1)


# ------------------ Генераторы контента ------------------
def generate_flowwow_title(name: str, is_mono: bool, positions: List[Dict[str, Any]], variation_label: str = "") -> str:
    """SEO-название Flowwow: для авторских — + 2 ключевых цветка; для моно — количество + название."""
    if is_mono:
        total_qty = sum(int(p.get("qty", 1)) for p in positions) or ""
        return f"{total_qty} {name}".strip() if total_qty else name

    top = [p["name"] for p in positions
           if "лента" not in p["name"].lower() and "упаковка" not in p["name"].lower()][:2]
    flowers_str = " с " + " и ".join(f.lower() for f in top) if top else ""
    size_str = f" {variation_label}" if variation_label else ""
    return f"{name}{size_str}{flowers_str}".strip()


def generate_flowwow_description(name: str, positions: List[Dict[str, Any]], style: str = "нежных") -> str:
    """Описание Flowwow: 600+ знаков, эмодзи, SEO. Без слова «бесплатно» (запрещено правилами)."""
    flowers_list = ", ".join(p["name"] for p in positions) or name
    return (
        f"Авторский букет «{name}» от цветочной студии REN, Санкт-Петербург 🌿\n\n"
        f"Состав: {flowers_list}. Игра {style} оттенков, воздушная фактура и фирменная "
        f"сборка REN — композиция выглядит так, будто её только собрали в саду 🌸\n\n"
        f"Кому дарить: любимой девушке 💕, маме 🤱, подруге, коллеге. Повод: день рождения 🎂, "
        f"юбилей, свидание, годовщина — или просто без повода, чтобы сделать день теплее ✨\n\n"
        f"В комплекте: фирменная открытка для вашего текста, аквабокс с водой для транспортировки "
        f"и кризал для стойкости цветов.\n\n"
        f"Уход: подрежьте стебли под углом 45°, поставьте букет в прохладную воду с кризалом "
        f"и меняйте воду каждые 1–2 дня — цветы останутся свежими дольше."
    )


def generate_tilda_seo_and_text(name: str, positions: List[Dict[str, Any]], style: str = "авторский") -> Dict[str, str]:
    """SEO-комплект Tilda: Title/Description/Keywords, 6 alt-текстов, продающий текст."""
    flowers_sample = ", ".join(p["name"] for p in positions[:2]) if positions else "цветами"
    title = f"Букет «{name}» с {flowers_sample} в СПб — купить с доставкой | REN"
    desc = (f"Закажите букет «{name}» в Санкт-Петербурге от мастерской REN. Свежие премиум-сорта, "
            f"аквабокс в подарок, фото перед отправкой. Быстрая доставка по СПб.")
    keywords = (f"купить букет {name.lower()} спб, доставка цветов санкт-петербург, "
                f"авторский букет ren, цветы на день рождения спб")
    prod_text = (
        f"Авторский букет «{name}» собран в узнаваемом почерке студии REN: сложная колористика, "
        f"воздух между бутонами и игра фактур. Состав: {flowers_sample}.\n\n"
        f"Букет доставляется на воде в аквабоксе и фирменной упаковке. "
        f"В комплекте — открытка и кризал для продления жизни цветов."
    )
    return {
        "title": title[:70],
        "description": desc[:170],
        "keywords": keywords,
        "text": prod_text,
        "alt_1": f"Букет {name} общий вид с доставкой в СПб",
        "alt_2": f"Букет {name} в руках флориста студия REN",
        "alt_3": f"Состав и бутоны букета {name} крупным планом",
        "alt_4": f"Фактурная сборка букета {name} в стиле REN вид сверху",
        "alt_5": f"Фирменная упаковка букета {name} с лентами студия REN",
        "alt_6": f"Букет {name} в интерьере цветочной мастерской REN"
    }


def generate_yandex_eda_row(base_art: str, name: str, size: str, base_price: float,
                            positions: List[Dict[str, Any]], cover_url: str) -> Dict[str, Any]:
    """Строка данных Яндекс Еды (авторские букеты) по регламенту ТЗ.
    ВНИМАНИЕ: CSV-импорт ЛК НЕ принимает юр. поля — okpd2/compliance/state_reg
    проставляются в редакторе карточки отдельным шагом."""
    prices = calculate_platform_prices(base_price)
    top_flower = (positions[0]["name"] if positions else "цветами").lower()
    weights = {"S": 0.6, "M": 0.9, "L": 1.4}
    eda_desc = (
        f"Авторский букет «{name}» в фирменном стиле студии REN. "
        f"В составе: {', '.join(p['name'] for p in positions)}. "
        f"Идеальный подарок на день рождения или свидание. "
        f"Доставляется в аквабоксе с водой, в комплекте открытка и кризал."
    )
    return {
        "id": f"{base_art}-{size}",
        "name": f"Авторский букет «{name}» {size} • с {top_flower}"[:45],
        "category": "Авторские букеты",
        "price": prices["yandex_eda"],
        "description": eda_desc,
        "weight": weights.get(size.upper(), 1.0),
        "unit": "шт",
        "in_pack": 1,
        "manufacturer": "REN",
        "country": "Россия",
        "okpd2": "011921190",
        "tnved": "0603197000",
        "picture": cover_url,
        "compliance": "Не требуется",
        "state_reg": "Не требуется"
    }


# ------------------ Яндекс Маркет: SEO и подсчёты ------------------
MARKET_COLOR_IDS = {
    "белый": 17946836, "кремовый": 14898800, "розовый": 17367592,
    "красный": 14898056, "сиреневый": 14896158, "жёлтый": 15271148,
    "зеленый": None, "зелёный": None, "темный": None, "разноцветный": None
}

EXCLUDE_FROM_STEMS = (
    "упаковка", "пленк", "плёнк", "лента", "кризал", "открытка",
    "аквабокс", "коробка", "корейк", "тишью", "фоамиран", "подкормка", "бант"
)


def market_color_value_id(color_label: str):
    """Маппинг цвета из меню бота на id значения Маркета (14871214)."""
    c = (color_label or "").lower()
    for name, vid in MARKET_COLOR_IDS.items():
        if name in c:
            return vid
    return None


def count_stems(positions: List[Dict[str, Any]]) -> int:
    """Реальное число цветов: без упаковки, лент, открыток, аквабокса, кризала."""
    total = 0
    for p in positions or []:
        name = str(p.get("name", "")).lower()
        if any(exc in name for exc in EXCLUDE_FROM_STEMS):
            continue
        try:
            total += int(p.get("qty", 1))
        except (TypeError, ValueError):
            total += 1
    return total or 1


def generate_market_seo_description(name: str, composition_str: str,
                                    height_cm: int, width_cm: int, packaging: str) -> str:
    """SEO-описание Маркета: 700+ знаков, LSI-ключи. Без запрещённых слов:
    «подарок», «бесплатно», «скидка», «хит», «новинка» — иначе карточку режут."""
    return (
        f"Авторский букет «{name}» от цветочной студии REN в Санкт-Петербурге.\n\n"
        f"Состав букета: {composition_str}. Высота — {height_cm} см, диаметр — {width_cm} см. "
        f"Оформление: {packaging.lower()}.\n\n"
        f"Букет подойдёт на день рождения, юбилей, свидание, годовщину или как знак внимания "
        f"без повода — для любимой девушки, мамы, подруги или коллеги. Свежие цветы сортируются "
        f"флористами в день сборки, поэтому букет долго держит форму и насыщенность оттенков.\n\n"
        f"В комплекте: фирменная открытка с вашим текстом, транспортировочный аквабокс с водой "
        f"и кризал для продления стойкости цветов.\n\n"
        f"Студия REN: авторская сборка профессиональными флористами, строгий отбор свежих "
        f"цветов на поставке, бережная доставка по Санкт-Петербургу в защитном боксе.\n\n"
        f"Уход: обновите срез стеблей под углом 45°, используйте прохладную воду с кризалом, "
        f"меняйте воду каждые 1–2 дня, держите букет вдали от прямого солнца и отопительных приборов."
    )


FILTER_COLOR_IDS = {
    "белый": 13887686, "кремовый": 13887677, "розовый": 13891903,
    "красный": 13891866, "сиреневый": 13898990, "жёлтый": 13891805,
    "зеленый": 13891809, "зелёный": 13891809, "темный": 13899071,
    "разноцветный": 15727440
}


def market_filter_color_id(color_label: str):
    c = (color_label or "").lower()
    for name, vid in FILTER_COLOR_IDS.items():
        if name in c:
            return vid
    return None


# ------------------ Очистка названий из МойСклада ------------------
MSL_ABBR = [
    ("хрз", "хризантема"),
    ("однг", "одноголовая"),
    ("хризантема одноголовая", "хризантема"),
]

# Позиции, которые НЕ показываем покупателю (упаковка, расходники)
DISPLAY_EXCLUDES = EXCLUDE_FROM_STEMS


def clean_position_name(raw: str) -> str:
    """'хрз однг БИГУДИ' -> 'Хризантема Бигуди'."""
    name = str(raw or "").strip().lower()
    for src, dst in MSL_ABBR:
        name = name.replace(src, dst)
    name = " ".join(name.split())
    # Титульный регистр: 'хризантема бигуди' -> 'Хризантема Бигуди'
    return name.title() if name else str(raw or "").strip()


def display_positions(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Состав для показа покупателю: без упаковки/лент/открыток, с чистыми названиями."""
    out = []
    for p in positions or []:
        raw = str(p.get("name", ""))
        low = raw.lower()
        if any(exc in low for exc in DISPLAY_EXCLUDES):
            continue
        try:
            qty = int(p.get("qty", 1))
        except (TypeError, ValueError):
            qty = 1
        out.append({"name": clean_position_name(raw), "qty": qty})
    return out
