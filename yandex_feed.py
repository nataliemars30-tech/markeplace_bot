"""Генератор YML-фида для Яндекс Бизнес / Яндекс Карт (витрина №5 по Мастер-ТЗ).

Цена в фиде = базовая + 10% (строго равна цене на сайте Tilda).
Раздача: маршрут /feed.xml на фото-сервере бота (порт Railway).
"""

import logging
from xml.etree import ElementTree as ET
from datetime import datetime
from typing import List, Dict, Any
from db import get_all_products_for_feed
from platform_rules import calculate_platform_prices

logger = logging.getLogger(__name__)

CAT_MAPPING = {
    "Букеты": "1",
    "Свадебные": "2",
    "Композиции": "3",
    "Декор": "4",
    "Доп. товары": "5"
}


def build_yml_xml(products: List[Dict[str, Any]]) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    yml_catalog = ET.Element("yml_catalog", date=now_str)
    shop = ET.SubElement(yml_catalog, "shop")

    ET.SubElement(shop, "name").text = "Цветочная студия REN"
    ET.SubElement(shop, "company").text = "REN Flowers"
    ET.SubElement(shop, "url").text = "https://ren-flowers.ru"

    currencies = ET.SubElement(shop, "currencies")
    ET.SubElement(currencies, "currency", id="RUB", rate="1")

    categories = ET.SubElement(shop, "categories")
    for cat_name, cat_id in CAT_MAPPING.items():
        ET.SubElement(categories, "category", id=cat_id).text = cat_name

    offers = ET.SubElement(shop, "offers")
    for prod in products:
        full_article = prod.get("full_article", "")
        if not full_article:
            continue

        offer = ET.SubElement(offers, "offer", id=full_article, available="true")
        name = prod.get("name", "Букет")
        var_label = prod.get("variation")
        offer_name = f"{name} {var_label}".strip() if var_label else name
        ET.SubElement(offer, "name").text = offer_name

        prices = calculate_platform_prices(float(prod.get("price", 0)))
        ET.SubElement(offer, "price").text = str(prices["yandex_maps"])
        ET.SubElement(offer, "currencyId").text = "RUB"

        root_cat = prod.get("root_category", "Букеты")
        ET.SubElement(offer, "categoryId").text = CAT_MAPPING.get(root_cat, "1")

        if prod.get("cover_url"):
            ET.SubElement(offer, "picture").text = prod["cover_url"]

        ET.SubElement(offer, "description").text = (
            f"Авторский букет от студии REN (Санкт-Петербург). "
            f"Свежие премиум-сорта, фирменная упаковка, аквабокс и открытка."
        )

    return ET.tostring(yml_catalog, encoding="utf-8", xml_declaration=True).decode("utf-8")


def get_feed_xml() -> str:
    """Сборка фида «на лету» из актуальной базы."""
    try:
        products = get_all_products_for_feed()
        xml = build_yml_xml(products)
        logger.info(f"YML-фид собран. Позиций: {len(products)}")
        return xml
    except Exception as e:
        logger.error(f"Ошибка сборки YML-фида: {e}")
        return '<?xml version="1.0" encoding="utf-8"?><yml_catalog date=""></yml_catalog>'
