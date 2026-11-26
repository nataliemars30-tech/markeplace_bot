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
        height_cm: int = 40,
        color: str = "",
        include_consist: bool = True,
        stock: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None
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

        from rules_sheet import get as rule_get
        item: Dict[str, Any] = {
            "offerId": offer_id,
            "productType": 1,
            "subCategoryId": sub_category_id,
            "name": name,
            "price": f"{int(price)}",
            "discount": "0",
            "description": description or composition or name,
            "images": photo_urls,
            "minOrderCount": 1,
            "width": {"val": width_cm, "measure": 3},
            "height": {"val": height_cm, "measure": 3},
            # Производитель — обязательное поле карточки (правило владельца, лист «Правила»)
            # manufacturerAddress НЕДОПУСТИМО для букетов (зависит от подкатегории) —
            # Flowwow отклоняет всю карточку с «Поле недопустимо» (16.11, проверено)
            "manufacturerName": rule_get("flowwow", "производитель_название", "REN, Россия"),
            "manufacturerCountry": 1,  # Россия (справочник subCategoryProperties)
        }
        # Наличие: stock ИЛИ productionTime (взаимоисключающие, обязательно одно).
        # Букеты — под заказ («изготовление 1 минута», measure 7 = минуты).
        # Растения — «в наличии»: передаём stock, productionTime не шлём.
        if stock is not None:
            item["stock"] = int(stock)
        else:
            item["productionTime"] = {"val": 1, "measure": 7}
        # Тематические подборки: у букетов и растений (sub 14) РАЗНЫЕ справочники
        # (subCategoryProperties 26.11). Растения: 1,32,33,34,40,48,53,159.
        if sub_category_id == 14:
            item["thematicSelections"] = [1, 32, 33, 34, 40, 48, 53, 159]
        else:
            item["thematicSelections"] = [1, 32, 33, 34, 39, 40, 48, 53, 102, 159]
        # Состав: у растений (sub 14) поля consist НЕТ в схеме — не отправляем вовсе.
        # API сохраняет количество ТОЛЬКО из ключа "val" — "quantity" игнорирует
        # и молча ставит 1 шт (инцидент 25.11, проверено 26.11)
        if include_consist and sub_category_id != 14:
            item["consist"] = [
                {**c, "val": int(c.get("val") or c.get("quantity") or 1)}
                for c in (consist or [{"name": name, "quantity": 1, "measure": 1}])
            ]
        # Доп. поля подкатегории (для растений: wateringFrequency, careLevel и т.п.)
        if extra:
            item.update(extra)
        # Цвет товара (справочник Flowwow) — из гаммы букета
        fw_color_map = {
            "белый": 10, "кремовый": 14, "розовый": 2, "красный": 1,
            "сиреневый": 15, "жёлтый": 4, "желтый": 4, "зеленый": 5,
            "темный": 9, "разноцветный": 11, "оранжевый": 3,
        }
        if color:
            low = str(color).lower()
            for word, cid in fw_color_map.items():
                if word in low:
                    item["color"] = cid
                    break

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

    async def set_top18(self, offer_ids: List[str]) -> Dict[str, Any]:
        """Замена списка топ-18 витрины. Формат: {"offers": [{"offerId": ...}]}
        (подтверждено валидацией 18.11). Только товары с offerId!"""
        url = f"{self.BASE_URL}/products/setTop18?shopId={self.shop_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._get_headers(),
                                     json={"offers": [{"offerId": o} for o in offer_ids]})
        try:
            data = resp.json()
        except Exception:
            raise FlowwowAPIError(f"Flowwow не-JSON [{resp.status_code}]: {resp.text[:200]}")
        if resp.status_code != 200 or data.get("errors"):
            raise FlowwowAPIError(f"Flowwow: {json.dumps(data.get('errors'), ensure_ascii=False)[:250]}")
        return data

    async def hide_products(self, offer_ids: List[str]) -> Dict[str, Any]:
        """Массовое скрытие (до 1000). Формат offers: [{\"offerId\": ...}] (18.11, проверено)."""
        url = f"{self.BASE_URL}/products/hide?shopId={self.shop_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._get_headers(),
                                     json={"offers": [{"offerId": o} for o in offer_ids]})
        try:
            data = resp.json()
        except Exception:
            raise FlowwowAPIError(f"Flowwow не-JSON [{resp.status_code}]: {resp.text[:200]}")
        if resp.status_code != 200 or data.get("errors"):
            raise FlowwowAPIError(f"Flowwow: {json.dumps(data.get('errors'), ensure_ascii=False)[:250]}")
        return {"success": True, "data": data}

    async def unhide_products(self, offer_ids: List[str]) -> Dict[str, Any]:
        """Массовое открытие (до 1000)."""
        url = f"{self.BASE_URL}/products/unhide?shopId={self.shop_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._get_headers(),
                                     json={"offers": [{"offerId": o} for o in offer_ids]})
        try:
            data = resp.json()
        except Exception:
            raise FlowwowAPIError(f"Flowwow не-JSON [{resp.status_code}]: {resp.text[:200]}")
        if resp.status_code != 200 or data.get("errors"):
            raise FlowwowAPIError(f"Flowwow: {json.dumps(data.get('errors'), ensure_ascii=False)[:250]}")
        return {"success": True, "data": data}

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

    async def get_product(self, offer_id: Optional[str] = None,
                          product_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Полная карточка товара по offerId или productId (для команды /sync)."""
        url = f"{self.BASE_URL}/products"
        body: Dict[str, Any] = {"shopId": self.shop_id}
        if offer_id:
            body["offerIds"] = [offer_id]
        if product_id:
            body["productIds"] = [product_id]
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, headers=self._get_headers(), json=body)
        try:
            data = resp.json()
            for item in data.get("items", []):
                if offer_id and item.get("offerId") == offer_id:
                    return item
                if product_id and item.get("productId") == product_id:
                    return item
        except Exception:
            pass
        return None

    async def list_products(self, page: int = 0, limit: int = 50,
                            status: str = "active") -> Dict[str, Any]:
        """Листинг товаров магазина (для /fwlist)."""
        url = f"{self.BASE_URL}/products"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, headers=self._get_headers(),
                                     json={"shopId": self.shop_id, "status": status,
                                           "page": page, "limit": limit})
        try:
            return resp.json()
        except Exception:
            return {"items": [], "total": 0}

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

    async def update_product(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Полное обновление существующей карточки (все актуальные поля обязательны).

        Документация: «Изменение товаров» — POST /apiseller/products/update.
        """
        url = f"{self.BASE_URL}/products/update?shopId={self.shop_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._get_headers(), json={"products": [item]})
        try:
            data = resp.json()
        except Exception:
            raise FlowwowAPIError(f"Flowwow не-JSON [{resp.status_code}]: {resp.text[:250]}")
        if resp.status_code != 200:
            raise FlowwowAPIError(f"Flowwow [{resp.status_code}]: {data.get('message') or str(data)[:300]}")
        return {"success": True, "data": data}

    async def get_reviews(self, page: int = 1, per_page: int = 50) -> List[Dict[str, Any]]:
        """Свежие отзывы магазина (sort=date_desc).

        Проверено живым вызовом (2026-11-07): GET /apiseller/shops/review?shopId=...
        Элементы: id, text, shopScore, courierScore, authorName, date,
        answerShop (пустая строка = магазин ещё не ответил), фото товара.
        Вебхуков для отзывов у Flowwow нет — только поллинг.
        """
        url = f"{self.BASE_URL}/shops/review?shopId={self.shop_id}&sort=date_desc&page={page}&perPage={per_page}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=self._get_headers())
        try:
            data = resp.json()
        except Exception:
            raise FlowwowAPIError(f"Flowwow не-JSON [{resp.status_code}]: {resp.text[:200]}")
        if resp.status_code != 200:
            raise FlowwowAPIError(f"Flowwow: {data.get('message') or data}")
        return data.get("items", [])

    async def answer_review(self, review_id: int, text: str) -> Dict[str, Any]:
        """Публичный ответ магазина на отзыв.

        Проверено (2026-11-07): POST /apiseller/review/answer?shopId=...
        Тело: {"reviewId": N, "answer": "текст"}; несуществующий id -> 404.
        """
        url = f"{self.BASE_URL}/review/answer?shopId={self.shop_id}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, headers=self._get_headers(),
                                     json={"reviewId": review_id, "answer": text})
        try:
            data = resp.json()
        except Exception:
            raise FlowwowAPIError(f"Flowwow не-JSON [{resp.status_code}]: {resp.text[:200]}")
        if resp.status_code != 200 or data.get("status") == 0:
            raise FlowwowAPIError(f"Flowwow: {data.get('message') or data}")
        return {"success": True, "data": data}

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
