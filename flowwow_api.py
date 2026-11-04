"""Интеграция с API маркетплейса Flowwow."""

import logging
from typing import Dict, Any, List, Optional
import httpx
from config import config

logger = logging.getLogger(__name__)


class FlowwowAPIError(Exception):
    pass


class FlowwowClient:
    BASE_URL = "https://apis.flowwow.com/apiseller"

    def __init__(self, token: Optional[str] = None, shop_id: Optional[int] = None):
        self.token = token or config.flowwow_token
        self.shop_id = shop_id or config.flowwow_shop_id

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    async def create_product(
        self,
        name: str,
        price: float,
        offer_id: str,
        sub_category_id: int,
        photo_urls: List[str],
        consist: Optional[List[Dict[str, Any]]] = None,
        composition: str = "",
        production_time: int = 60,
        width_cm: int = 30,
        height_cm: int = 40,
        weight_g: int = 500
    ) -> Dict[str, Any]:
        """
        Создание товара на Flowwow по спецификации Seller API.

        Обязательные поля карточки (см. subCategoryProperties):
        offerId, productType, subCategoryId, name, price,
        productionTime (measure: 7=минуты) ИЛИ stock,
        minOrderCount, width/height (measure: 3=см),
        consist (measure: 1=шт), volume (measure: 2=г),
        composition, quantityInPackage.
        Фото передаются в поле images (массив URL), первое — обложка.
        """
        url = f"{self.BASE_URL}/products"

        payload = {
            "shopId": self.shop_id,
            "productType": 1,
            "subCategoryId": sub_category_id,
            "offerId": offer_id,
            "name": name,
            "price": str(int(price)),
            "discount": 0,
            "description": composition,
            "images": photo_urls,
            "productionTime": {"val": production_time, "measure": 7},
            "minOrderCount": 1,
            "width": {"val": width_cm, "measure": 3},
            "height": {"val": height_cm, "measure": 3},
            "consist": consist or [{"name": name, "quantity": 1, "measure": 1}],
            "volume": {"val": weight_g, "measure": 2},
            "composition": composition or name,
            "quantityInPackage": 1,
            "isActive": False,
            "stock": 0
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(url, headers=self._get_headers(), json=payload)
            except httpx.RequestError as e:
                raise FlowwowAPIError(f"Ошибка сети при обращении к Flowwow: {e}")

            if resp.status_code in (200, 201):
                res_data = resp.json()
                flowwow_id = res_data.get("id") or res_data.get("productId") or "created"
                return {"success": True, "id": str(flowwow_id), "data": res_data}

            err_text = resp.text
            try:
                err_json = resp.json()
                err_text = err_json.get("message") or err_json.get("error") or resp.text
            except Exception:
                pass

            raise FlowwowAPIError(f"Ошибка валидации Flowwow API [{resp.status_code}]: {err_text}")
