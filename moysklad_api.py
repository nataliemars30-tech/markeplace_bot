"""Слой работы с API МойСклад Remap 1.2: извлечение данных из Заказа покупателя."""

import logging
from typing import Dict, Any, Optional, List
import httpx
from config import config

logger = logging.getLogger(__name__)


class MoySkladAuthError(Exception):
    pass


class MoySkladAPIError(Exception):
    pass


class MoySkladClient:
    BASE_URL = "https://api.moysklad.ru/api/remap/1.2"

    def __init__(self, token: Optional[str] = None):
        self.token = token or config.moysklad_token

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json"
        }

    async def get_customer_order_details(self, order_name_or_id: str) -> Dict[str, Any]:
        """
        Поиск документа «Заказ покупателя» по номеру/названию.
        Извлекает:
          - Общую сумму продажи (руб)
          - Рассчитанную себестоимость по закупочным ценам компонентов (руб)
          - Список позиций с наименованиями и количеством
        """
        clean_query = order_name_or_id.strip()
        url = f"{self.BASE_URL}/entity/customerorder"
        params = {"search": clean_query, "limit": 5}

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.get(url, headers=self._get_headers(), params=params)
            except httpx.RequestError as e:
                raise MoySkladAPIError(f"Сетевой сбой при обращении к МойСклад: {e}")

            if resp.status_code == 401:
                raise MoySkladAuthError(
                    "Токен доступа МойСклад (MOYSKLAD_TOKEN) просрочен или недействителен. "
                    "Обновите токен в файле .env"
                )

            if resp.status_code != 200:
                raise MoySkladAPIError(f"Ошибка МойСклад [{resp.status_code}]: {resp.text}")

            data = resp.json()
            rows = data.get("rows", [])
            if not rows:
                raise MoySkladAPIError(f"Заказ покупателя с номером '{clean_query}' не найден в МойСклад.")

            order = rows[0]
            order_id = order.get("id")
            selling_sum_rub = order.get("sum", 0) / 100.0

            # Загружаем позиции заказа
            pos_resp = await client.get(
                f"{self.BASE_URL}/entity/customerorder/{order_id}/positions",
                headers=self._get_headers()
            )
            positions_data = pos_resp.json().get("rows", []) if pos_resp.status_code == 200 else []

            total_cost_rub = 0.0
            items_breakdown = []

            for pos in positions_data:
                qty = pos.get("quantity", 1)
                assortment = pos.get("assortment", {})
                item_name = assortment.get("name", "Цветок")

                # Пытаемся получить закупочную цену
                # В МойСклад buyPrice может храниться в мета-ссылке ассортимента
                buy_price_rub = 0.0
                assortment_href = assortment.get("meta", {}).get("href")
                if assortment_href:
                    try:
                        assort_resp = await client.get(assortment_href, headers=self._get_headers())
                        if assort_resp.status_code == 200:
                            assort_obj = assort_resp.json()
                            item_name = assort_obj.get("name", item_name)
                            buy_price_obj = assort_obj.get("buyPrice", {})
                            buy_price_rub = buy_price_obj.get("value", 0) / 100.0
                    except Exception as ex:
                        logger.warning(f"Не удалось получить закупочную цену для {item_name}: {ex}")

                item_cost = buy_price_rub * qty
                total_cost_rub += item_cost
                items_breakdown.append({
                    "name": item_name,
                    "qty": qty,
                    "buy_price": buy_price_rub,
                    "total_cost": item_cost
                })

            return {
                "order_id": order_id,
                "order_name": order.get("name"),
                "selling_sum": selling_sum_rub,
                "cost_price": total_cost_rub,
                "positions": items_breakdown
            }
