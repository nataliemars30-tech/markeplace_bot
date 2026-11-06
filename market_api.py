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


# Характеристики категории 91284 (id из POST /v2/category/91284/parameters)
PARAMS = {
    "Состав": 84321304,
    "Количество цветов": 14805873,
    "Цвет товара для карточки": 14871214,
    "Вид цветов": 43672710,
    "Оформление": 37920070,
    "Кому": 27078630,
    "Повод": 27141030,
    "Высота букета": 14805336,
    "Сборка": 43867410,
    "Размер": 32835410,
    "Единиц в одной упаковке": 14805583,
    "Количество упаковок в товаре": 33663230,
}
SIZE_VALUE_IDS = {"S": 32835890, "M": 32835539, "L": 32835431,
                  "XL": 32835748, "XS": 32835944}
OFORM_ACQUABOX = 44624283  # «аквабокс» — всегда в комплекте у REN

# Вид цветов: популярные значения справочника (дополним по мере нужды)
FLOWER_VALUE_IDS = {
    "роза": 44406335,
}


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
        weight_kg: float = 0.9,
        diameter_cm: int = 30,
        height_pack_cm: int = 45,
        okpd2: str = "01.19.21.190",
        tnved: str = "0603197000",
        packaging_film: bool = False,
        color_value_id: Optional[int] = None
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
            {"parameterId": PARAMS["Оформление"], "valueId": 44624283, "value": "аквабокс"},
            {"parameterId": PARAMS["Сборка"], "valueId": 44625852, "value": "на стеблях"},
            # Кому: женщине, любимой, маме, коллеге
            {"parameterId": PARAMS["Кому"], "valueId": 44406928, "value": "женщине"},
            {"parameterId": PARAMS["Кому"], "valueId": 44406925, "value": "любимой"},
            {"parameterId": PARAMS["Кому"], "valueId": 27079070, "value": "маме"},
            {"parameterId": PARAMS["Кому"], "valueId": 27079058, "value": "коллеге"},
            # Повод: день рождения, юбилей, 8 Марта, День матери, Новый год
            {"parameterId": PARAMS["Повод"], "valueId": 28804646, "value": "день рождения"},
            {"parameterId": PARAMS["Повод"], "valueId": 28804641, "value": "юбилей"},
            {"parameterId": PARAMS["Повод"], "valueId": 28804652, "value": "8 Марта"},
            {"parameterId": PARAMS["Повод"], "valueId": 28804658, "value": "День матери"},
            {"parameterId": PARAMS["Повод"], "valueId": 28804648, "value": "Новый год"},
        ]
        # Оформление: плёнка или под ленту (по факту упаковки из состава МойСклада)
        if packaging_film:
            parameter_values.append(
                {"parameterId": PARAMS["Оформление"], "valueId": 44624290, "value": "пленка"})
        else:
            parameter_values.append(
                {"parameterId": PARAMS["Оформление"], "valueId": 44624261, "value": "под ленту"})
        size_id = SIZE_VALUE_IDS.get(str(size_label).upper())
        if size_id:
            parameter_values.append(
                {"parameterId": PARAMS["Размер"], "valueId": size_id, "value": str(size_label).upper()})
        if color_value_id:
            parameter_values.append(
                {"parameterId": PARAMS["Цвет товара для карточки"], "valueId": color_value_id})

        offer = {
            "offerId": offer_id,
            "name": name[:256],
            "marketCategoryId": self.CATEGORY_FLOWERS,
            "pictures": pictures[:30],
            "vendor": "REN",
            "description": description[:6000],
            "basicPrice": {"value": float(price), "currencyId": "RUR"},
            "weightDimensions": {"length": diameter_cm, "width": diameter_cm,
                                 "height": height_pack_cm, "weight": weight_kg},
            "boxCount": 1,
            "shelfLife": {"timePeriod": 7, "timeUnit": "DAY"},
            "lifeTime": {"timePeriod": 7, "timeUnit": "DAY"},
            "guaranteePeriod": {"timePeriod": 1, "timeUnit": "DAY"},
            "commodityCodes": [
                {"type": "OKPD2_CODE", "code": okpd2},
                {"type": "CUSTOMS_COMMODITY_CODE", "code": tnved}
            ],
            "manufacturerCountries": ["Россия"],
            "vendorCode": offer_id,
            "parameterValues": parameter_values
        }

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
            raise YandexMarketError(f"Маркет [{resp.status_code}]: {json.dumps(data, ensure_ascii=False)[:300]}")
        results = (data.get("result") or {}).get("results") or []
        for r in results:
            if r.get("offerId") == offer_id:
                errors = r.get("errors") or []
                if errors:
                    raise YandexMarketError(f"Карточка отклонена: {errors[:2]}")
                warnings = r.get("warnings") or []
                if warnings:
                    logger.info(f"Маркет warnings для {offer_id}: {warnings[:2]}")
        if data.get("status") == "ERROR":
            raise YandexMarketError(str(data.get("errors"))[:300])
        return {"success": True, "offerId": offer_id}
