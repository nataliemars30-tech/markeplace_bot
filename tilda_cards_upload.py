"""Открытки с Flowwow -> Tilda (рубрика «Декор»)."""
import asyncio, json, sys, types as T
sys.path.insert(0, ".")
m = T.ModuleType("config"); m.config = T.SimpleNamespace(openai_api_key="", tilda_mcp_token="", tilda_project_id=13756635)
sys.modules["config"] = m
_mcp = json.load(open(r"C:/Users/наташа/.kimi-code/mcp.json", encoding="utf-8"))
TOKEN = _mcp["mcpServers"]["tilda"]["headers"]["Authorization"].replace("Bearer ", "").strip()
import tilda_mcp as TM
from yandex_feed import _clean

async def main():
    full = json.load(open("fw_products_full.json", encoding="utf-8"))
    cards = [p for p in full if p.get("isActive") and "открытка" in (p.get("name") or "").lower()]
    print(f"открыток: {len(cards)}", flush=True)
    cat = TM.TildaCatalog(); cat.client = TM.TildaMCPClient(token=TOKEN); cat._available = True
    ok = err = 0
    for p in cards:
        name = _clean(p["name"])[:190]
        price = int(round((float(p["price"]) / 1.5 - 150) * 1.1 / 10) * 10)
        if price <= 0:
            price = int(round(float(p["price"]) / 10) * 10)  # дешёвые: без наценки-вычета
        try:
            res = await cat.create_product(
                project_id=13756635, title=name, price_tilda=price,
                photo_urls=[u for u in (p.get("images") or []) if u.startswith("http")][:3],
                tilda_categories=["Декор"],
                description=f"{name} — фирменная открытка цветочной студии REN. Дополните букет тёплыми словами.",
                full_article=f"CARD-{p['productId']}",
                seo_title=f"{name} — купить в СПб | REN"[:200], seo_descr=name,
                seo_keywords="открытка к букету, купить открытку спб",
                height_cm=15, weight_g=50, width_cm=10)
            print("+", name[:50], price, "->", res[:50], flush=True)
            ok += 1
        except Exception as e:
            print("ERR", name[:40], str(e)[:80], flush=True); err += 1
        await asyncio.sleep(3)
    print(f"ГОТОВО: {ok} ок, {err} ошибок", flush=True)

asyncio.run(main())
