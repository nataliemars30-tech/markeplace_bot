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
        height_cm: int = 40
    ) -> Dict[str, Any]:
        """
        Создание товара на Flowwow.

        Проверено живыми вызовами (2026-11-04):
        - Эндпоинт: POST /apiseller/products/create?shopId=<id>
          (POST /products без /create — это ЛИСТИНГ, молча игнорирует тело!)
        - Тело: {"products": [ {<поля товара>} ]}, до 500 за раз
        - Набор допустимых полей зависит от подкатегории
          (источник: POST /categories/subCategoryProperties).
          Для букетов (subCategoryId 1-2) поля volume/composition/quantityInPackage
          НЕДОПУСТИМЫ — API отвечает "Поле недопустимо".
        - stock и productionTime взаимоисключающие: передаём productionTime.
        - Ответ: {"summary": {...}, "data": [{"offerId","productId"}], "errors": [...]}
        - Фото грузятся асинхронно, до 30 мин до появления в интерфейсе.
        """
        url = f"{self.BASE_URL}/products/create?shopId={self.shop_id}"

        item: Dict[str, Any] = {
            "offerId": offer_id,
            "productType": 1,
            "subCategoryId": sub_category_id,
            "name": name,
            "price": f"{int(price)}",
            "discount": "0",
            "description": composition or name,
            "images": photo_urls,
            "productionTime": {"val": production_time, "measure": 7},
            "minOrderCount": 1,
            "width": {"val": width_cm, "measure": 3},
            "height": {"val": height_cm, "measure": 3},
            "consist": consist or [{"name": name, "quantity": 1, "measure": 1}]
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(url, headers=self._get_headers(), json={"products": [item]})
            except httpx.RequestError as e:
                raise FlowwowAPIError(f"Ошибка сети при обращении к Flowwow: {e}")

            try:
                res_data = resp.json()
            except Exception:
                raise FlowwowAPIError(f"Flowwow вернул не-JSON [{resp.status_code}]: {resp.text[:300]}")

            summary = res_data.get("summary", {})
            if summary.get("success", 0) >= 1 and res_data.get("data"):
                product_id = res_data["data"][0].get("productId", "created")
                return {"success": True, "id": str(product_id), "data": res_data}

            err_details = res_data.get("errors") or res_data.get("message") or resp.text[:300]
            raise FlowwowAPIError(f"Flowwow не создал товар: {err_details}")
