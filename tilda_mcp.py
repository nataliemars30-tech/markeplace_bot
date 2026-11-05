"""Прямой клиент MCP-сервера Тильды (https://tilda.ru/api/mcp/, Bearer-токен).

Позволяет боту на Railway создавать товары в каталоге Tilda 24/7 —
без браузера и без посредников. Токен: TILDA_MCP_TOKEN (Профиль → Ключи MCP).
Протокол: MCP streamable HTTP (JSON-RPC поверх POST, сессия через Mcp-Session-Id).
"""

import json
import logging
import uuid
from typing import Any, Dict, Optional

import httpx
from config import config

logger = logging.getLogger(__name__)


class TildaMCPError(Exception):
    pass


class TildaMCPClient:
    URL = "https://tilda.ru/api/mcp/"
    PROTOCOL = "2025-03-26"

    def __init__(self, token: Optional[str] = None):
        self.token = token or getattr(config, "tilda_mcp_token", "")
        self.session_id: Optional[str] = None

    async def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(self.URL, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise TildaMCPError(f"MCP HTTP {resp.status_code}: {resp.text[:250]}")
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            self.session_id = sid
        ctype = resp.headers.get("content-type", "")
        if "event-stream" in ctype:
            data_line = ""
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    data_line = line[5:].strip()
            return json.loads(data_line) if data_line else {}
        return resp.json()

    async def _ensure_init(self) -> None:
        if self.session_id:
            return
        await self._post({
            "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "initialize",
            "params": {
                "protocolVersion": self.PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "ren-marketplace-bot", "version": "1.0"}
            }
        })
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        await self._ensure_init()
        res = await self._post({
            "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call",
            "params": {"name": name, "arguments": arguments}
        })
        if res.get("error"):
            raise TildaMCPError(str(res["error"])[:300])
        content = res.get("result", {}).get("content", [])
        return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")


# Маппинг рубрик меню бота -> partuid каталога Tilda (из store_get_parts_list)
TILDA_PARTUIDS = {
    "Стиль REN": "474818273072",
    "Премиум": "139437642792",
    "Нежные": "975880065602",
    "Монобукеты": "261002116812",
    "Розы": "101723699802",
    "Декор": "778369579862",
    "Весенние": "704466005492",
    "Свадебные": "241430425742",
    "День Матери": "415483346843",
}


class TildaCatalog:
    """Создание товаров в каталоге Tilda из данных бота."""

    def __init__(self):
        self.client = TildaMCPClient()
        self._available = bool(self.client.token)

    async def create_product(
        self,
        *,
        project_id: int,
        title: str,
        price_tilda: int,
        photo_urls: list,
        tilda_categories: list,
        description: str,
        full_article: str,
    ) -> str:
        if not self._available:
            return "нет токена (TILDA_MCP_TOKEN)"
        partuids = ",".join(
            TILDA_PARTUIDS[c] for c in (tilda_categories or []) if c in TILDA_PARTUIDS
        )
        gallery = json.dumps([{"img": u} for u in photo_urls], ensure_ascii=False)
        try:
            # 1. создать товар (получить uid)
            created = await self.client.call_tool("store_create_products", {
                "projectid": project_id,
                "title": title[:190],
                "storepartuid": partuids.split(",")[0] if partuids else None
            })
            import re as _re
            m = _re.search(r"\b(\d{6,})\b", created)
            product_uid = m.group(1) if m else None
            # 2. заполнить поля
            await self.client.call_tool("store_save_products", {
                "projectid": project_id,
                "productuid": product_uid,
                "title": title[:190],
                "price": str(price_tilda),
                "partuids": partuids,
                "gallery": gallery,
                "descr": description[:1000],
                "sku": full_article,
                "externalid": full_article
            })
            return f"создан (uid {product_uid})"
        except Exception as e:
            logger.warning(f"Tilda MCP ошибка: {e}")
            return f"ошибка ({e})"
