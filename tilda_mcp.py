"""Прямой клиент MCP-сервера Тильды (https://tilda.ru/api/mcp/, Bearer-токен).

Позволяет боту на Railway создавать товары в каталоге Tilda 24/7 —
без браузера и без посредников. Токен: TILDA_MCP_TOKEN (Профиль → Ключи MCP).
Протокол: MCP streamable HTTP (JSON-RPC поверх POST, сессия через Mcp-Session-Id).
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from config import config

logger = logging.getLogger(__name__)


class TildaMCPError(Exception):
    pass


class TildaMCPClient:
    URL = "https://tilda.ru/api/mcp/"
    PROTOCOL = "2026-07-28"  # поддерживаемые сервером версии; 2025-03-26 отклоняется с 400

    def __init__(self, token: Optional[str] = None):
        self.token = token or getattr(config, "tilda_mcp_token", "")
        self.session_id: Optional[str] = None

    # Tilda MCP диспетчеризует запросы по заголовкам (проверено 2026-11-07):
    # tools/call требует Mcp-Method + Mcp-Name и _meta в params.
    META = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }

    async def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.PROTOCOL,
        }
        if payload.get("method") == "tools/call":
            headers["Mcp-Method"] = "tools/call"
            headers["Mcp-Name"] = payload.get("params", {}).get("name", "")
            payload["params"]["_meta"] = self.META
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(self.URL, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise TildaMCPError(f"MCP HTTP {resp.status_code}: {resp.text[:250]}")
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            self.session_id = sid
        ctype = resp.headers.get("content-type", "")
        if not resp.content:
            return {}  # 202 Accepted с пустым телом (notifications/initialized)
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
    "Осенние": "554935962393",
}

# Постоянные доп. товары для букетов (опции каталога Tilda)
TILDA_OPTIONS_TITLE = "Дополнительно"
TILDA_OPTIONS = [
    {"value": "Открытка фирменная", "price": "150"},
    {"value": "Транспортировочный набор (коробка и аквабокс)", "price": "490"},
    {"value": "Секатор", "price": "900"},
]


class TildaCatalog:
    """Создание товаров в каталоге Tilda из данных бота."""

    def __init__(self):
        self.client = TildaMCPClient()
        self._available = bool(self.client.token)
        self.last_report = ""  # детальная авто-проверка последнего товара

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
        photo_alts: Optional[list] = None,
        seo_title: str = "",
        seo_descr: str = "",
        seo_keywords: str = "",
        height_cm: int = 45,
        weight_g: int = 900
    ) -> str:
        if not self._available:
            return "нет токена (TILDA_MCP_TOKEN)"
        partuids = ",".join(
            TILDA_PARTUIDS[c] for c in (tilda_categories or []) if c in TILDA_PARTUIDS
        )
        # Фото: загружаем на CDN Tilda (внешние ссылки в gallery часто не подгружаются)
        cdn_urls = await self._upload_photos(project_id, photo_urls)
        # gallery: у каждого фото SEO alt-текст
        gallery_items = []
        for i, u in enumerate(cdn_urls):
            item = {"img": u}
            if photo_alts and i < len(photo_alts) and photo_alts[i]:
                item["alt"] = photo_alts[i]
            gallery_items.append(item)
        gallery = json.dumps(gallery_items, ensure_ascii=False)
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
            # 2. заполнить поля (partuids — ВСЕ рубрики, не только первая)
            await self.client.call_tool("store_save_products", {
                "projectid": project_id,
                "productuid": product_uid,
                "title": title[:190],
                "price": str(price_tilda),
                "partuids": partuids,
                "gallery": gallery,
                "descr": description[:1000],
                "sku": full_article,
                "externalid": full_article,
                "seo_title": seo_title[:200],
                "seo_descr": seo_descr[:400],
                "seo_keywords": seo_keywords[:500],
                # размеры букета (мм) и вес (г)
                "pack_x": "250", "pack_y": "250", "pack_z": str(int(height_cm) * 10),
                "pack_m": str(int(weight_g)),
                # постоянные доп. товары-опции
                "prod_option": TILDA_OPTIONS_TITLE,
                "prod_variants": json.dumps(TILDA_OPTIONS, ensure_ascii=False),
            })
            self.last_report = self._verify_product(project_id, product_uid, {
                "title": title, "price": str(price_tilda), "partuids": partuids,
                "expected_photos": len(photo_urls), "seo_title": seo_title,
                "full_article": full_article,
            })
            first_line = self.last_report.splitlines()[0]
            return first_line
        except Exception as e:
            logger.warning(f"Tilda MCP ошибка: {e}")
            return f"ошибка ({e})"

    async def _upload_photos(self, project_id: int, photo_urls: list) -> list:
        """Загрузка фото на CDN Tilda через инструмент upload_image.

        Сырьё с камер бывает до 8000+ px — Tilda CDN отказывает (>8000px).
        Перед загрузкой ресайзим через ffmpeg до 2000px по большей стороне
        (ffmpeg есть в контейнере — используется для видео).
        Возвращает CDN-URL'ы; при сбое — исходные URL (фолбэк)."""
        import base64
        import re as _re
        import subprocess
        import tempfile
        cdn = []
        for u in photo_urls or []:
            try:
                async with httpx.AsyncClient(timeout=120.0) as http:
                    raw = await http.get(u)
                    raw.raise_for_status()
                with tempfile.TemporaryDirectory() as td:
                    src = Path(td) / "src.img"
                    dst = Path(td) / "dst.jpg"
                    src.write_bytes(raw.content)
                    resized = subprocess.run(
                        ["ffmpeg", "-y", "-i", str(src),
                         "-vf", "scale='min(2000,iw)':-2", str(dst)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    payload = dst.read_bytes() if resized.returncode == 0 and dst.exists() else raw.content
                res = await self.client.call_tool("upload_image", {
                    "projectid": project_id,
                    "images": [{
                        "filename": "photo.jpg",
                        "image_base64": base64.b64encode(payload).decode(),
                        "mimetype": "image/jpeg",
                    }],
                })
                m = _re.search(r"https://[^\s\"']*tildacdn[^\s\"']+", res or "")
                cdn.append(m.group(0) if m else u)
            except Exception as e:
                logger.warning(f"Tilda upload_image сбой для {u}: {e} — оставляю исходный URL")
                cdn.append(u)
        return cdn

    def _verify_product(self, project_id: int, product_uid: str, expected: Dict[str, Any]) -> str:
        """Авто-проверка опубликованного товара: бот сам сверяет, что Tilda
        реально сохранила все поля, и выдаёт итог ✅/⚠️ (ручная проверка не нужна)."""
        try:
            raw = self.client.call_tool("store_get_products", {
                "projectid": project_id, "productuid": int(product_uid)
            })
            data = json.loads(raw) if isinstance(raw, str) else raw
            checks = []
            ok = True

            def check(label: str, cond: bool, detail: str = ""):
                nonlocal ok
                checks.append(f"{'✅' if cond else '⚠️'} {label}{' — ' + detail if detail else ''}")
                ok = ok and cond

            title = data.get("title", "")
            check("название", title == expected["title"][:190], title[:60])
            price = str(data.get("price", "")).split(".")[0]
            check("цена", price == str(expected["price"]), f"{price}₽")
            gallery = json.loads(data.get("gallery") or "[]")
            with_alt = [g for g in gallery if g.get("alt")]
            check("фото", len(gallery) == expected["expected_photos"],
                  f"{len(gallery)}/{expected['expected_photos']}")
            check("alt у фото", len(with_alt) == len(gallery), f"{len(with_alt)}/{len(gallery)}")
            parts = (data.get("partuids") or "").split(",")
            want_parts = [p for p in expected["partuids"].split(",") if p]
            check("рубрики", all(p in parts for p in want_parts),
                  f"{len([p for p in want_parts if p in parts])}/{len(want_parts)}")
            check("SEO title", bool(data.get("seo_title")))
            check("SEO descr", bool(data.get("seo_descr")))
            check("SEO keywords", bool(data.get("seo_keywords")))
            check("артикул", data.get("externalid") == expected["full_article"],
                  str(data.get("externalid")))
            check("описание", bool(data.get("descr")))

            status = "проверен" if ok else "создан с замечаниями"
            return f"{status} (uid {product_uid})\n" + "\n".join(checks)
        except Exception as e:
            logger.warning(f"Tilda verify ошибка: {e}")
            return f"создан (uid {product_uid}), авто-проверка не удалась: {e}"
