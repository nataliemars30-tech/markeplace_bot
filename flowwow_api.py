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
        description: str = "",
        production_time: int = 5,
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
            "description": description or composition or name,
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

    async def hide_product(self, offer_id: str) -> Dict[str, Any]:
        """Скрытие товара с витрины (кнопка «Скрыть букет со всех витрин»)."""
        url = f"{self.BASE_URL}/products/hide?shopId={self.shop_id}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, headers=self._get_headers(), json={"offerId": offer_id})
        try:
            data = resp.json()
        except Exception:
            raise FlowwowAPIError(f"Flowwow вернул не-JSON [{resp.status_code}]: {resp.text[:200]}")
        if resp.status_code == 200 and (data.get("updated") or data.get("isHidden")):
            return {"success": True}
        raise FlowwowAPIError(f"Flowwow: {data.get('message') or data}")

    async def get_product_url(self, offer_id: str) -> Optional[str]:
        """Получить публичную ссылку на карточку товара по offerId."""
        url = f"{self.BASE_URL}/products"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, headers=self._get_headers(),
                                     json={"shopId": self.shop_id, "offerIds": [offer_id]})
        try:
            data = resp.json()
            for item in data.get("items", []):
                if item.get("offerId") == offer_id and item.get("url"):
                    return item["url"]
        except Exception:
            pass
        return None

    async def archive_product(self, offer_id: str) -> Dict[str, Any]:
        """Перенос товара в архив (команда /purge)."""
        url = f"{self.BASE_URL}/products/archive?shopId={self.shop_id}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, headers=self._get_headers(), json={"offerId": offer_id})
        try:
            data = resp.json()
        except Exception:
            raise FlowwowAPIError(f"Flowwow не-JSON [{resp.status_code}]: {resp.text[:200]}")
        if resp.status_code == 200 and data.get("isArchived"):
            return {"success": True}
        raise FlowwowAPIError(data.get("message") or f"архивация не удалась: {data}")
