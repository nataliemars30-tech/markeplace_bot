"""Интеграция с API Яндекс Маркета (партнёрский, v2).

Принцип: создаём ТОЛЬКО новые товары (offerId = артикул REN-NNN-...).
Существующие карточки не трогаем (решение владельца, 05.11.2026).
Проверено живыми вызовами: создание — POST /v2/campaigns/{id}/offers/update
(POST /offers без /update — это ЛИСТИНГ, молча игнорирует тело!).
Кампания: DBS 149020930 (живая). FBS 148843101 заблокирована — НЕ используем.
"""

import logging
from typing import Dict, Any, List, Optional
import httpx
from config import config

logger = logging.getLogger(__name__)


class YandexMarketError(Exception):
    pass


class YandexMarketClient:
    BASE_URL = "https://api.partner.market.yandex.ru/v2"
    CAMPAIGN_DBS = 149020930  # живая кампания REN (DBS)

    def __init__(self, api_key: Optional[str] = None, campaign_id: Optional[int] = None):
        self.api_key = api_key or getattr(config, "yandex_market_api_key", "") or _env("YANDEX_MARKET_API_KEY")
        self.campaign_id = campaign_id or self.CAMPAIGN_DBS

    def _headers(self) -> Dict[str, str]:
        return {"Api-Key": self.api_key, "Content-Type": "application/json"}

    async def create_offer(
        self,
        offer_id: str,
        name: str,
        price: int,
        picture_urls: Optional[List[str]] = None,
        weight_kg: float = 0.9,
        length_mm: int = 350,
        width_mm: int = 350,
        height_mm: int = 450
    ) -> Dict[str, Any]:
        """Создание нового товара в DBS-кампании. Существующие не изменяет."""
        offer: Dict[str, Any] = {
            "offerId": offer_id,
            "name": name,
            "category": "Цветы",
            "price": {"value": float(price), "currencyId": "RUR"},
            "weightDimensions": {
                "length": length_mm, "width": width_mm,
                "height": height_mm, "weight": weight_kg
            }
        }
        if picture_urls:
            offer["pictures"] = picture_urls[:10]

        url = f"{self.BASE_URL}/campaigns/{self.campaign_id}/offers/update"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(url, headers=self._headers(), json={"offers": [offer]})
            except httpx.RequestError as e:
                raise YandexMarketError(f"Сетевой сбой Маркета: {e}")

            if resp.status_code == 200 and resp.json().get("status") == "OK":
                return {"success": True, "offerId": offer_id}
            raise YandexMarketError(f"Маркет отклонил: [{resp.status_code}] {resp.text[:300]}")


def _env(key: str) -> str:
    import os
    return os.environ.get(key, "")
