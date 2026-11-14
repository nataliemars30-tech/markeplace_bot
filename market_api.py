"""Интеграция с API Яндекс Маркета: полные карточки через business API.

Проверено живыми вызовами (05.11.2026):
- Полный контент карточки: POST /v2/businesses/{businessId}/offer-mappings/update
  (offerId, name, marketCategoryId, pictures, vendor, description + характеристики)
- Чтение статуса карточки: POST /v2/businesses/{businessId}/offer-cards
- Категория «Цветы, букеты, композиции» = 91284 (листовая), кабинет = 216468683.
Старый campaign-level offers/update создавал только «скелеты» — заменён этим модулем.
Существующие карточки не трогаем: создаём только новые offerId REN-NNN-...
"""

import json
import logging
from typing import Dict, Any, List, Optional
import httpx
from config import config

logger = logging.getLogger(__name__)


class YandexMarketError(Exception):
    pass


# Характеристики категории 91284 (полная схема из POST /v2/category/91284/parameters, 07.11.2026)
PARAMS = {
    "Состав": 84321304,
    "Количество цветов": 14805873,
    "Цвет товара для карточки": 14871214,
    "Цвет товара для фильтра": 13887626,
    "Тип": 21194330,
    "Вид цветов": 43672710,
    "Зелень": 43674870,
    "Оформление": 37920070,
    "Кому": 27078630,
    "Повод": 27141030,
    "Высота букета": 14805336,
    "Сборка": 43867410,
    "Тип свадебного букета": 43867490,
    "Размер": 32835410,
    "Единиц в одной упаковке": 14805583,
    "Количество упаковок в товаре": 33663230,
    "Дополнительная информация": 7351754,
    "Прочие характеристики": 57046341,
}
SIZE_VALUE_IDS = {"S": 32835890, "M": 32835539, "L": 32835431,
                  "XL": 32835748, "XS": 32835944}
OFORM_IDS = {"Пленка": 44624290, "Под ленту": 44624261,
             "Корзина": 44624275, "Коробка": 44624259}  # Оформление (37920070)
# Тип товара (21194330) по смыслу
TYPE_IDS = {"mono": (34064842, "монобукет"), "author": (34070791, "сборный букет"),
            "box": (34069690, "композиция в коробке"),
            "basket": (34066674, "композиция в корзине"),
            "vase": (34063873, "композиция в вазе")}
BOUQUET_WIDTH_CM = 25  # ширина букета по регламенту владельца

# Кому (27078630) — отмечаем ВСЕ значения справочника
ALL_AUDIENCE = [
    (27078890, "бабушке"), (44406927, "деловому партнеру"), (44406928, "женщине"),
    (27079058, "коллеге"), (44406925, "любимой"), (27079070, "маме"),
    (44406919, "мужчине"), (44406922, "ребенку"), (44406911, "учителю"),
]

# Зелень (43674870, multivalue)
GREEN_KIND_MAP = [
    ("эвкалипт", 44406474, "ветви эвкалипта"),
    ("писташ", 44406488, "зелень фисташки"),
    ("аспарагус", 44406536, "аспарагус"),
    ("буплерум", 44406418, "буплерум"),
    ("гиперикум", 44406457, "гиперикум"),
    ("рускус", 44406520, "листья рускуса"),
    ("салал", 44406518, "листья салала"),
]

# Цвет товара для фильтра (13887626)
FILTER_COLOR_IDS = {
    "белый": 13887686, "кремовый": 13887677, "розовый": 13891903,
    "красный": 13891866, "сиреневый": 13898990, "жёлтый": 13891805,
    "зеленый": 13891809, "зелёный": 13891809, "темный": 13899071,
    "разноцветный": 15727440
}

        # Вид цветов (43672710, multivalue): фраза -> (valueId, каноническое название для value)
FLOWER_KIND_MAP = [
    ("роза пионовидная", 44406358, "роза пионовидная"),
    ("роза кустовая", 44406375, "роза кустовая"),
    ("пионовидная роза", 44406358, "роза пионовидная"),
    ("роза", 44406335, "роза"),
    ("гвоздика", 44406343, "гвоздика"),
    ("хризантема", 44406402, "хризантема"),
    ("тюльпан", 44406361, "тюльпан"),
    ("пион", 44406333, "пион"),
    ("гортензия", 44406404, "гортензия"),
    ("альстромерия", 44406363, "альстромерия"),
    ("гербера", 44406357, "гербера"),
    ("гипсофила", 44406384, "гипсофила"),
    ("маттиол", 44406342, "маттиола"),
    ("дельфиниум", 44406330, "дельфиниум"),
    ("анемона", 44406328, "анемона"),
    ("ранункул", 44406341, "лютик"),
    ("лютик", 44406341, "лютик"),
    ("калла", 44406397, "калла"),
    ("ирис", 44406322, "ирис"),
    ("фрезия", 44406414, "фрезия"),
    ("лилия", 44406430, "лилия"),
    ("подсолнух", 44406431, "подсолнух"),
    ("георгин", 44406381, "георгин"),
    ("амариллис", 44406399, "амариллис"),
    ("астра", 44406317, "астра"),
    ("нарцисс", 44406413, "нарцисс"),
    ("орхидея", 44406318, "орхидея"),
    ("протея", 44406417, "протея"),
    ("статица", 44406339, "статица"),
    ("нутан", 44406385, "нутан"),
    ("гладиолус", 44406327, "гладиолус"),
    ("сирень", 44406394, "сирень"),
    ("мимоза", 44406348, "мимоза"),
    ("лаванда", 44406412, "лаванда"),
    ("василек", 44406429, "василек"),
    ("антуриум", None, ""), ("эустома", None, "")  # нет в справочнике Маркета
]

# Зелень и наполнители — не относим к «Виду цветов»
_NOT_FLOWER_WORDS = ("эвкалипт", "писташ", "раскус", "аспарагус", "буплерум", "зелень",
                     "салал", "рускус", "питтоспорум", "гиперикум", "розмарин", "литопс")


def match_flower_kinds(names: List[str]) -> List[tuple]:
    """Определяет виды цветов по названиям позиций. Понимает склонения/множественное:
    'розы', 'роз', 'пионовидные розы' -> роза; 'гвоздики' -> гвоздика."""
    found = []
    added = set()
    for pname in names or []:
        low = str(pname).lower().replace("ё", "е")
        words = [w.strip(",.()«»") for w in low.split()]
        if any(g in low for g in _NOT_FLOWER_WORDS):
            continue
        for phrase, vid, canonical in FLOWER_KIND_MAP:
            if not vid or vid in added:
                continue
            if phrase in low:
                found.append((vid, canonical))
                added.add(vid)
                continue
            # морфология: 'розы'/'роз' по основе 'роза'->'роз', 'тюльпаны'->'тюльпан'
            stem = phrase[:-1] if phrase.endswith(("а", "я")) else phrase
            if len(stem) >= 3 and any(w.startswith(stem) for w in words):
                found.append((vid, canonical))
                added.add(vid)
    return found

# Повод (27141030) — отмечаем ВСЕ значения справочника
ALL_OCCASIONS = [
    (28804668, "23 февраля"), (28804652, "8 Марта"), (28804655, "выпускной"),
    (28804654, "День знаний"), (28804658, "День матери"), (28804651, "День Победы"),
    (28804646, "день рождения"), (28804653, "день свадьбы"),
    (28804663, "День святого Валентина"), (34582590, "День семьи"),
    (28804664, "День учителя"), (28804656, "Масленица"), (28804648, "Новый год"),
    (28804650, "Пасха"), (28804657, "рождение ребенка"), (28804647, "Рождество"),
    (28804670, "Хэллоуин"), (28804641, "юбилей"),
]


def _dotted_okpd2(code: str) -> str:
    """'011921190' -> '01.19.21.190' (требование Маркета: точки обязательны)."""
    c = str(code or "").replace(".", "").strip()
    if len(c) == 9:
        return f"{c[0:2]}.{c[2:4]}.{c[4:6]}.{c[6:9]}"
    return str(code or "")


_CARD_COLOR_NAMES = {17946836: "белый", 14898800: "светло-бежевый", 17367592: "розовый",
                     14898056: "красный", 14896158: "сиреневый", 15271148: "жёлтый"}
_FILTER_COLOR_NAMES = {13887686: "белый", 13887677: "бежевый", 13891903: "розовый",
                       13891866: "красный", 13898990: "фиолетовый", 13891805: "желтый",
                       13891809: "зеленый", 13899071: "черный", 15727440: "разноцветный"}


def _color_name(vid: int) -> str:
    return _CARD_COLOR_NAMES.get(vid, "")


def _filter_color_name(vid: int) -> str:
    return _FILTER_COLOR_NAMES.get(vid, "")


class YandexMarketClient:
    BASE_URL = "https://api.partner.market.yandex.ru/v2"
    BUSINESS_ID = 216468683
    CAMPAIGN_DBS = 149020930
    CATEGORY_FLOWERS = 91284

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or getattr(config, "yandex_market_api_key", "")
        if not self.api_key:
            import os
            self.api_key = os.environ.get("YANDEX_MARKET_API_KEY", "")

    def _headers(self) -> Dict[str, str]:
        return {"Api-Key": self.api_key, "Content-Type": "application/json"}

    async def create_card(
        self,
        *,
        offer_id: str,
        name: str,
        price: int,
        pictures: List[str],
        description: str,
        composition: str,
        total_flowers: int,
        height_cm: int,
        size_label: str = "",
        weight_kg: float = 1.0,
        diameter_cm: int = 30,
        height_pack_cm: int = 45,
        okpd2: str = "01.19.21.190",
        tnved: str = "0603197000",
        packaging: str = "Пленка",
        color_value_id: Optional[int] = None,
        bouquet_type: str = "author",
        flower_names: Optional[List[str]] = None,
        filter_color_id: Optional[int] = None,
        video_url: Optional[str] = None,
        width_cm: int = 25,
        product_noun: str = "Букет"
    ) -> Dict[str, Any]:
        """Полноценная карточка товара в каталоге кабинета. Только НОВЫЕ offerId.

        Исправления от 06.11.2026 (по разбору ошибок валидации):
        - габариты: у букета length = width = диаметр, height = высота стеблей;
        - сроки для живых цветов (shelfLife/lifeTime 7 дней, гарантия 1 день) в корректном
          формате TimePeriodDTO {timePeriod, timeUnit};
        - boxCount=1 и «Количество упаковок»=1 (иначе ошибка «Слишком маленькое значение»);
        - полный набор характеристик: Кому, Повод, Оформление (аквабокс + плёнка/лента),
          Сборка, размер, цвет, состав, высота, число цветов.
        """

        parameter_values: List[Dict[str, Any]] = [
            {"parameterId": PARAMS["Состав"], "value": composition[:2000]},
            {"parameterId": PARAMS["Количество цветов"], "value": str(total_flowers)},
            {"parameterId": PARAMS["Высота букета"], "value": str(height_cm)},
            {"parameterId": PARAMS["Единиц в одной упаковке"], "value": "1"},
            {"parameterId": PARAMS["Количество упаковок в товаре"], "value": "1"},
            # Оформление: ТОЛЬКО плёнка или под ленту (аквабокс никому не указываем — правило владельца)
            {"parameterId": PARAMS["Сборка"], "valueId": 44625852, "value": "на стеблях"},
            {"parameterId": PARAMS["Дополнительная информация"],
             "value": f"{product_noun} собирается флористом в день доставки. "
                      f"Цветочная студия REN, Санкт-Петербург."},
            # Прочие характеристики: формат «Название:значение» (требование Маркета)
            {"parameterId": PARAMS["Прочие характеристики"],
             "value": "Вариативность состава:состав и оттенки могут незначительно меняться в зависимости "
                      "от поставки, флорист сохраняет настроение и цветовую гамму букета"},
        ]
        # Кому: отмечаем ВСЕ значения справочника (максимум баллов)
        for vid, vname in ALL_AUDIENCE:
            parameter_values.append(
                {"parameterId": PARAMS["Кому"], "valueId": vid, "value": vname})
        # Повод: отмечаем ВСЕ значения справочника (требование владельца)
        for vid, vname in ALL_OCCASIONS:
            parameter_values.append(
                {"parameterId": PARAMS["Повод"], "valueId": vid, "value": vname})
        # Оформление: плёнка / под ленту / корзина / коробка (аквабокс НЕ указываем)
        oform = OFORM_IDS.get(packaging, OFORM_IDS["Пленка"])
        parameter_values.append(
            {"parameterId": PARAMS["Оформление"], "valueId": oform, "value": packaging.lower()})
        size_id = SIZE_VALUE_IDS.get(str(size_label).upper())
        if size_id:
            parameter_values.append(
                {"parameterId": PARAMS["Размер"], "valueId": size_id, "value": str(size_label).upper()})
        if color_value_id:
            parameter_values.append(
                {"parameterId": PARAMS["Цвет товара для карточки"], "valueId": color_value_id,
                 "value": _color_name(color_value_id)})
        if filter_color_id:
            parameter_values.append(
                {"parameterId": PARAMS["Цвет товара для фильтра"], "valueId": filter_color_id,
                 "value": _filter_color_name(filter_color_id)})
        # Тип: монобукет / сборный букет / композиция в коробке / в корзине / в вазе
        type_id, type_name = TYPE_IDS.get(bouquet_type, TYPE_IDS["author"])
        parameter_values.append(
            {"parameterId": PARAMS["Тип"], "valueId": type_id, "value": type_name})
        # Вид цветов (multivalue) — автоопределение по составу с морфологией
        for vid, canonical in match_flower_kinds(flower_names):
            parameter_values.append(
                {"parameterId": PARAMS["Вид цветов"], "valueId": vid, "value": canonical})

        # Зелень (multivalue) — по названиям позиций состава
        added_green = set()
        for pname in flower_names or []:
            low = str(pname).lower()
            for phrase, vid, canonical in GREEN_KIND_MAP:
                if vid and phrase in low and vid not in added_green:
                    parameter_values.append(
                        {"parameterId": PARAMS["Зелень"], "valueId": vid, "value": canonical})
                    added_green.add(vid)
                    break

        offer = {
            "offerId": offer_id,
            "name": name[:256],
            "marketCategoryId": self.CATEGORY_FLOWERS,
            "pictures": pictures[:30],
            "vendor": "REN",
            "description": description[:6000],
            "basicPrice": {"value": int(round(float(price))), "currencyId": "RUR"},
            # Габариты: ширина из карточки (25 по умолчанию, для корзин — от флориста),
            # длина = высота = стебли
            "weightDimensions": {"length": height_cm, "width": width_cm,
                                 "height": height_cm, "weight": weight_kg},
            "boxCount": 1,
            # ОКПД2 и срок годности НЕ передаём (решение владельца)
            "manufacturerCountries": ["Россия"],
            "vendorCode": offer_id,
            "parameterValues": parameter_values
        }
        if video_url:
            offer["videos"] = [video_url]

        url = f"{self.BASE_URL}/businesses/{self.BUSINESS_ID}/offer-mappings/update"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(url, headers=self._headers(),
                                         json={"offerMappings": [{"offer": offer}]})
            except httpx.RequestError as e:
                raise YandexMarketError(f"Сетевой сбой Маркета: {e}")

        try:
            data = resp.json()
        except Exception:
            raise YandexMarketError(f"Маркет вернул не-JSON [{resp.status_code}]: {resp.text[:250]}")

        if resp.status_code != 200:
            raise YandexMarketError(f"Маркет [{resp.status_code}]: {json.dumps(data, ensure_ascii=False)[:400]}")
        results = (data.get("result") or {}).get("results") or []
        for r in results:
            if r.get("offerId") == offer_id:
                errors = r.get("errors") or []
                if errors:
                    raise YandexMarketError(f"Карточка отклонена: {json.dumps(errors, ensure_ascii=False)[:400]}")
                warnings = r.get("warnings") or []
                if warnings:
                    logger.info(f"Маркет warnings для {offer_id}: {warnings[:2]}")
        if data.get("status") == "ERROR":
            raise YandexMarketError(f"Маркет ERROR: {json.dumps(data, ensure_ascii=False)[:400]}")
        return {"success": True, "offerId": offer_id}

    async def delete_offer(self, offer_id: str) -> Dict[str, Any]:
        """Удаление товара из DBS-кампании (команда /purge). Если ещё не проиндексирован —
        Маркет вернёт notDeleted, повторить позже или удалить в кабинете."""
        url = f"{self.BASE_URL}/campaigns/{self.CAMPAIGN_DBS}/offers/delete"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json={"offerIds": [offer_id]})
        try:
            data = resp.json()
        except Exception:
            raise YandexMarketError(f"Маркет не-JSON [{resp.status_code}]: {resp.text[:200]}")
        not_del = (data.get("result") or {}).get("notDeletedOfferIds") or []
        if data.get("status") == "OK" and offer_id not in not_del:
            return {"success": True}
        raise YandexMarketError("товар ещё не проиндексирован — удаление невозможно сейчас, "
                                "повторите позже или удалите в кабинете")
