"""Восстановление составов на Flowwow после инцидента с ключом количества.

API игнорировал 'quantity' -> везде стало 1. Реальные составы сохранены в
fw_details.json. Перезаписываем классическим API с подтверждённым ключом (QTY_KEY).
Запуск: python fw_restore_consist.py [qty|quantity|count]  (по умолчанию qty)
"""

import json
import sys
import time
import urllib.request

QTY_KEY = sys.argv[1] if len(sys.argv) > 1 else "qty"
EXPENDABLES = ("упаковк", "тишью", "лента", "кризал", "аквабокс", "открытка", "коробка",
               "оазис", "бант", "подкормка", "транспортировочн")
THEMES = [1, 150, 48, 53, 40, 32, 39, 33, 34, 102, 159]

sec = dict(l.strip().split("=", 1) for l in open(".secrets.local", encoding="utf-8") if "=" in l)
TOK, SID = sec["FLOWWOW_TOKEN"], sec["FLOWWOW_SHOP_ID"]
COLORS = {"красн": 1, "розов": 2, "оранж": 3, "жёлт": 4, "желт": 4, "зелен": 5, "зелён": 5,
          "син": 7, "фиолет": 8, "черн": 9, "чёрн": 9, "бел": 10, "голуб": 12, "бордо": 13,
          "крем": 14, "сирен": 15, "коралл": 16, "беж": 20}


def color_of(name):
    low = (name or "").lower()
    for k, v in COLORS.items():
        if k in low:
            return v
    return 11


def classic_post(path, body, tries=5):
    for a in range(tries):
        try:
            r = urllib.request.Request("https://apis.flowwow.com/apiseller" + path,
                                       data=json.dumps(body).encode(), method="POST",
                                       headers={"Authorization": f"Bearer {TOK}",
                                                "Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=60) as resp:
                return json.load(resp)
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(8)
    return {}


def main():
    items = json.load(open("fw_products_full.json", encoding="utf-8"))
    by_pid = {p["productId"]: p for p in items}
    details = json.load(open("fw_details.json", encoding="utf-8"))
    updated = set(json.load(open("fw_updated.json")))
    # затронутые: все обновлённые конвейером + 13 REN (обновлены 24.11)
    ren13 = [p["productId"] for p in items
             if (p.get("offerId") or "").startswith("REN-0")]
    targets = sorted(updated | set(ren13))
    print(f"к восстановлению: {len(targets)} (ключ количества: {QTY_KEY})", flush=True)
    try:
        done = set(json.load(open("fw_restored.json")))
    except Exception:
        done = set()
    ok = err = skip = 0
    for i, pid in enumerate(targets):
        if pid in done:
            continue
        p = by_pid.get(pid)
        raw = details.get(str(pid)) or []
        consist = []
        for c in raw:
            nm = (c.get("name") or "").strip()
            if not nm or any(e in nm.lower() for e in EXPENDABLES):
                continue
            q = int(c.get("quantity") or 1)
            consist.append({"name": nm, QTY_KEY: q, "quantity": q, "measure": 1})
        if not consist:
            skip += 1
            continue  # состава нет — не трогаем, разберём отдельно
        oid = (p.get("offerId") or "").strip() or f"REN-FW-{pid}"
        item = {
            "offerId": oid, "productType": p.get("type") or 1,
            "subCategoryId": p.get("subCategoryId") or 2,
            "name": p["name"], "price": str(int(float(p["price"]))),
            "discount": str(p.get("discount") or "0"),
            "description": p.get("description") or "",
            "images": p.get("images") or [],
            "productionTime": {"val": 1, "measure": 7},
            "minOrderCount": p.get("minOrder") or 1,
            "thematicSelections": THEMES,
            "width": {"val": 35, "measure": 3}, "height": {"val": 45, "measure": 3},
            "manufacturerName": "REN, Россия", "manufacturerCountry": 1,
            "color": color_of(p["name"]),
            "consist": consist,
        }
        try:
            d = classic_post(f"/products/update?shopId={SID}", {"products": [item]})
            if (d.get("summary") or {}).get("success"):
                done.add(pid)
                json.dump(sorted(done), open("fw_restored.json", "w"))
                ok += 1
            else:
                msgs = [e.get("message") for e in (d.get("errors") or [])]
                print(f"[{i+1}] ERR {pid}: {json.dumps(msgs, ensure_ascii=False)[:120]}", flush=True)
                err += 1
        except Exception as e:
            print(f"СБОЙ {pid}: {str(e)[:60]}", flush=True)
            err += 1
            time.sleep(30)
        if (i + 1) % 20 == 0:
            print(f"прогресс: {ok} ок / {err} ошибок / {skip} без состава ({i+1}/{len(targets)})", flush=True)
        time.sleep(0.8)
    print(f"ГОТОВО: восстановлено {ok}, ошибок {err}, без состава {skip}", flush=True)


if __name__ == "__main__":
    main()
