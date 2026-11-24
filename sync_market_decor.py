"""Синхронизация декора (вазы/кашпо) Flowwow -> Яндекс Маркет.

- Что включено на Flowwow, но выключено на Маркете (совпадения) — включаем (остаток 5) и обновляем цену.
- Новые позиции Flowwow — создаём карточки в категории «Вазы для цветов» (61853873).
- Выключенное на Flowwow на Маркете уже с нулевым остатком (проверено 24.11) — не трогаем.

Цена Маркета: base = fw/1.5 - 150; market = base * 1.5 (округление до десятков).
Запуск: python sync_market_decor.py [apply]
"""

import asyncio
import json
import re
import sys

import httpx

KEY = "ACMA:ysRdbQ8pShodeZQhhiiveJHTyqnDlLfEhURz0DGJ:d685be2b"
BID, CID = 216468683, 149020930
WAREHOUSE = 2164085
CAT_VASE = 61853873
H = {"Api-Key": KEY, "Content-Type": "application/json"}

EMOJI_RX = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️←-⇿⌀-⏿\U0001F1E6-\U0001F1FF]")
EXPENDABLES = ("набор",)  # «Весенний набор» уже есть на Маркете отдельно

# Совпадения Маркет <- Flowwow (включить, а не создавать)
MATCHED = {
    "MRKT-VIGWRBSQ": 74102478,   # тёмное стекло 12x22 №249
    "MRKT-BEC42BMP": 72012440,   # Classic glass #247
    "MRKT-MUWGT09K": 74102926,   # бутылка «Черничный»
    "MRKT-55ZU6PX3": 74543801,   # весенний набор тюльпаны+ваза
    "MRKT-O1NKMR9A": 74090091,   # «Авиньон» 11*20
}


def clean(s: str) -> str:
    return re.sub(r"\s{2,}", " ", EMOJI_RX.sub("", s or "")).strip(" ,-–—\"\\")


def market_price(fw_price: float) -> int:
    base = fw_price / 1.5 - 150
    return int(round(base * 1.5 / 10) * 10)


def norm_key(name: str) -> str:
    n = clean(name).lower()
    n = re.sub(r"\bсм\b", "", n)
    n = re.sub(r"[^\wа-яё ]", " ", n)
    return re.sub(r"\s{2,}", " ", n).strip()


def dims_from_name(name: str):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*[*xх×-]\s*(\d+(?:[.,]\d+)?)", name)
    if m:
        a = float(m.group(1).replace(",", "."))
        b = float(m.group(2).replace(",", "."))
        w, h = min(a, b), max(a, b)
        return int(w), int(h)
    m = re.search(r"\b(\d{2})\s*см", name)
    if m:
        return 12, int(m.group(1))
    return 12, 20


def build_name(fw_name: str) -> str:
    n = clean(fw_name)
    n = re.sub(r"\s*/\s*\d+\s*$", "", n)          # хвосты «/251»
    n = re.sub(r"\s*#?\d{3}\s*$", "", n)
    n = re.sub(r"\\\d+", "", n)
    # «Ваза ... » -> «Ваза REN ...», если REN нет
    if "ren" not in n.lower():
        n = re.sub(r"^(Ваза|Кашпо|Кружка)\b", r"\1 REN", n, count=1)
    if len(n) < 60:
        n = n + " — для букетов и интерьера, дизайнерская"
    return n[:120]


def build_offer(fw: dict) -> dict:
    name = build_name(fw["name"])
    w, h = dims_from_name(fw["name"])
    low = fw["name"].lower()
    material = "керамика" if "керамик" in low else "стекло"
    price = market_price(float(fw["price"]))
    desc = clean((fw.get("desc_full") or fw["name"]))
    if len(desc) < 200:
        desc = (f"{name}. Декор от цветочной студии REN (Санкт-Петербург): лаконичные формы "
                f"и азиатская эстетика. Подходит для букетов, сухоцветов и как самостоятельный "
                f"интерьерный объект. Материал: {material}. Размер: {w}x{h} см.")
    pics = (fw.get("images") or [])[:10]
    params = [
        {"parameterId": 21194330, "value": "кашпо" if "кашпо" in low else "ваза"},
        {"parameterId": 17352854, "value": material},
        {"parameterId": 14805336, "unitId": 8, "value": str(h)},
        {"parameterId": 23681530, "unitId": 8, "value": str(w)},
        {"parameterId": 14805583, "value": "1"},
        {"parameterId": 23674510, "unitId": 6146430, "value": "1"},
    ]
    return {
        "offerId": f"VASE-{fw['id']}",
        "name": name,
        "marketCategoryId": CAT_VASE,
        "pictures": pics,
        "vendor": "REN",
        "description": desc[:5900],
        "basicPrice": {"value": price, "currencyId": "RUR"},
        "weightDimensions": {"length": h, "width": w, "height": h, "weight": 1.0},
        "boxCount": 1,
        "manufacturerCountries": ["Россия"],
        "vendorCode": f"VASE-{fw['id']}",
        "parameterValues": params,
    }


async def set_stocks(c, pairs):
    """pairs: [(offerId, count)]"""
    body = {"skus": [{"offerId": oid, "warehouseId": WAREHOUSE,
                      "items": [{"type": "FIT", "count": n}, {"type": "AVAILABLE", "count": n}]}
                     for oid, n in pairs]}
    r = await c.put(f"https://api.partner.market.yandex.ru/v2/campaigns/{CID}/offers/stocks",
                    headers=H, json=body)
    if r.status_code != 200:
        r = await c.post(f"https://api.partner.market.yandex.ru/v2/campaigns/{CID}/offers/stocks",
                         headers=H, json=body)
    return r.status_code, r.text[:200]


async def main(apply: bool):
    fw = json.load(open("fw_decor_full.json", encoding="utf-8"))
    active = [p for p in fw if p["active"]]
    # дедуп по нормализованному имени: берём дешевле
    uniq = {}
    for p in active:
        k = norm_key(p["name"])
        if k not in uniq or float(p["price"]) < float(uniq[k]["price"]):
            uniq[k] = p
    matched_fw_ids = set(MATCHED.values())
    new_items = [p for k, p in uniq.items() if p["id"] not in matched_fw_ids]
    print(f"активных на FW: {len(active)} | уникальных: {len(uniq)} | совпадений с МК: {len(MATCHED)} | новых: {len(new_items)}\n")

    print("--- включить существующие (остаток 5, цена от FW) ---")
    for oid, fwid in MATCHED.items():
        p = next((x for x in fw if x["id"] == fwid), None)
        if p:
            print(f"  {oid} <- FW {fwid} | {p['name'][:50]} | цена МК {market_price(float(p['price']))}")

    print("\n--- новые карточки ---")
    offers = []
    for p in new_items:
        o = build_offer(p)
        offers.append(o)
        print(f"  {o['offerId']} | {o['name'][:70]} | {o['basicPrice']['value']}₽ | фото {len(o['pictures'])}")

    if not apply:
        print("\ndry-run. Применить: python sync_market_decor.py apply")
        return

    async with httpx.AsyncClient(timeout=90) as c:
        # 1. включаем совпадения: цена + остаток
        price_body = {"offers": []}
        for oid, fwid in MATCHED.items():
            p = next((x for x in fw if x["id"] == fwid), None)
            if p:
                price_body["offers"].append({"offerId": oid,
                                             "price": {"value": market_price(float(p["price"])), "currencyId": "RUR"}})
        r = await c.post(f"https://api.partner.market.yandex.ru/v2/businesses/{BID}/offer-prices/updates",
                         headers=H, json=price_body)
        print("цены совпадений:", r.status_code, r.text[:150])
        st, txt = await set_stocks(c, [(oid, 5) for oid in MATCHED])
        print("остатки совпадений:", st, txt[:150])

        # 2. создаём новые батчами по 10
        ok = err = 0
        for i in range(0, len(offers), 10):
            batch = offers[i:i + 10]
            r = await c.post(f"https://api.partner.market.yandex.ru/v2/businesses/{BID}/offer-mappings/update",
                             headers=H, json={"offerMappings": [{"offer": o} for o in batch]})
            d = r.json()
            if r.status_code == 200 and d.get("status") == "OK":
                for res in (d.get("result") or {}).get("results") or []:
                    if res.get("errors"):
                        err += 1
                        print("  ОШИБКА", res.get("offerId"), json.dumps(res["errors"], ensure_ascii=False)[:200])
                    else:
                        ok += 1
                if not d.get("result"):
                    ok += len(batch)
            else:
                err += len(batch)
                print("  ОШИБКА батча:", r.status_code, json.dumps(d, ensure_ascii=False)[:300])
            await asyncio.sleep(1)
        print(f"создано: {ok}, ошибок: {err}")
        # 3. остатки новым
        st, txt = await set_stocks(c, [(o["offerId"], 5) for o in offers])
        print("остатки новых:", st, txt[:150])


if __name__ == "__main__":
    asyncio.run(main(len(sys.argv) > 1 and sys.argv[1] == "apply"))
